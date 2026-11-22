"""Configured ordinary rooms and their tables."""

import re
import struct
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationInfo, model_validator
from pydantic.dataclasses import dataclass

from tmserver.protocol.identities import (
    GAME_KEY,
    ROOM_RAW,
    SHOP_RAW,
    TABLE_RAW,
    TRADE_RAW,
    game_id,
    service_nick,
)
from tmserver.rooms.table import OrdinaryTable
from tmserver.rooms.packets import ROOM_CHANNEL, TABLE_CHANNEL, TRADE_CHANNEL
from tmserver.rooms.records import build_room_record
from tmserver.rooms.service import RoomService
from tmserver.rooms.rules import RoomRules


CONFIG = ConfigDict(strict=True, extra="forbid", validate_default=True)
RawRoute = Annotated[int, Field(ge=0, le=0x1FFFFFFFFFF)]
SeatCount = Annotated[int, Field(ge=2, le=3)]


def _ascii_text(value, info: ValidationInfo):
    if info.mode == "json" and isinstance(value, str):
        return value.encode("ascii")
    return value


AsciiBytes = Annotated[bytes, BeforeValidator(_ascii_text)]


def _json_config(value, info: ValidationInfo):
    """Convert JSON arrays and seat shorthand; keep internal flags out of the format."""
    if info.mode == "json" and isinstance(value, dict):
        if "flexible_seats" in value:
            raise ValueError("use players='auto' to configure automatic seats")
        if "rules" in value and value["rules"] is None:
            raise ValueError("room rules must be an object")
        value = dict(value)
        if isinstance(value.get("tables"), list):
            value["tables"] = tuple(value["tables"])
        if value.get("players") == "auto":
            value.update(players=3, flexible_seats=True)
    return value


def _validate_seats(config):
    if config.mode == "computer" and config.players != 2:
        raise ValueError("computer tables require two seats")
    if config.flexible_seats and (config.mode != "versus" or config.players != 3):
        raise ValueError("automatic seats require a three-seat versus table")


@dataclass(frozen=True, config=CONFIG)
class OrdinaryTableConfig:
    table_raw: RawRoute
    table_channel: AsciiBytes
    mode: Literal["versus", "computer"] = "versus"
    players: SeatCount = 2
    flexible_seats: bool = False

    _parse_config = model_validator(mode="before")(_json_config)

    def __post_init__(self):
        _validate_seats(self)
        if not re.fullmatch(rb"#[A-Za-z0-9_-]{1,14}", self.table_channel):
            raise ValueError("table channel must fit its terminated sixteen-byte field")


@dataclass(frozen=True, config=CONFIG)
class OrdinaryRoomConfig:
    name: AsciiBytes
    room_raw: RawRoute
    table_raw: RawRoute
    room_channel: AsciiBytes
    table_channel: AsciiBytes
    mode: Literal["versus", "computer"] = "versus"
    players: SeatCount = 2
    tables: Annotated[tuple[OrdinaryTableConfig, ...], Field(max_length=126)] = ()
    flexible_seats: bool = False
    rules: RoomRules | None = None

    _parse_config = model_validator(mode="before")(_json_config)

    def __post_init__(self):
        if not re.fullmatch(rb"[ -~]{1,30}", self.name) or b"*" in self.name:
            raise ValueError("room name must fit thirty printable bytes without asterisk")
        _validate_seats(self)
        if (
            self.rules is not None
            and self.rules.gs != 0
            and any(t.mode == "computer" for t in (self, *self.tables))
        ):
            # The hosted Computer constructor sets gs=0, and its menu has no
            # departure-rule control. ComGameInit carries no override for it.
            raise ValueError("fixed rooms with Computer tables require departure rule gs=0")
        RoomService(
            self.players,
            room_id=game_id(self.room_raw),
            table_id=game_id(self.table_raw),
            room_channel=self.room_channel,
            table_channel=self.table_channel,
        )


class _RoomDocument(BaseModel):
    model_config = CONFIG
    rooms: list[OrdinaryRoomConfig]


def load_ordinary_rooms(path):
    return tuple(_RoomDocument.model_validate_json(Path(path).read_bytes()).rooms)


def configure_rooms(host, configs):
    configs = tuple(configs)
    ids = {game_id(i) for i in (0, ROOM_RAW, TABLE_RAW, SHOP_RAW, TRADE_RAW)}
    ids.update(game_id(raw) for raw in range(1, len(host.accounts) + 1))
    channels = {ROOM_CHANNEL, TABLE_CHANNEL, TRADE_CHANNEL}
    names = {b"Local Room"}
    for config in host.events.configs.values():
        ids.update((config.room_id, config.init_id, config.card_id))
        channels.update((config.init_channel.upper(), config.card_channel.upper()))
        if config.listing.key_region == b"Local Lobby":
            names.add(config.listing.key_event)
    for binding in host.event_rooms.rooms:
        channels.add(binding.room_channel.upper())
    for _, identity, channel in host.event_matches.routes.values():
        ids.add(identity)
        channels.add(channel.upper())
    # Validate all routes before constructing any runtime or editing metadata.
    for config in configs:
        if not isinstance(config, OrdinaryRoomConfig):
            raise ValueError("ordinary room configuration type required")
        if any(
            t.mode == "versus" and t.players > len(host.accounts) for t in (config, *config.tables)
        ):
            raise ValueError("ordinary table needs more enabled accounts")
        if config.name in names:
            raise ValueError("ordinary room display key collides")
        names.add(config.name)
        for raw in (config.room_raw, config.table_raw, *(t.table_raw for t in config.tables)):
            identity = game_id(raw)
            if identity in ids:
                raise ValueError("ordinary room/table identity collides")
            ids.add(identity)
        for channel in (
            config.room_channel,
            config.table_channel,
            *(t.table_channel for t in config.tables),
        ):
            if channel.upper() in channels:
                raise ValueError("ordinary room/table channel collides")
            channels.add(channel.upper())
    listed = 1 + sum(binding.zone_index == 0 for binding in host.event_rooms.rooms)
    if listed + len(configs) > 256:
        raise ValueError("ordinary rooms exceed native directory capacity")
    host.ordinary_room_configs = configs
    default = host.default_table
    tables = [default]
    bindings = []
    for config in configs:
        room = RoomService(
            config.players,
            room_id=game_id(config.room_raw),
            table_id=game_id(config.table_raw),
            room_channel=config.room_channel,
            table_channel=config.table_channel,
            computer_enabled=config.mode == "computer",
        )
        for index, table_config in enumerate((config, *config.tables)):
            slot = (
                room
                if index == 0
                else room.add_table(
                    game_id(table_config.table_raw),
                    table_config.table_channel,
                    table_config.players,
                    computer_enabled=table_config.mode == "computer",
                )
            )
            table = OrdinaryTable(
                host,
                slot,
                match_mode=table_config.mode,
                match_player_count=table_config.players,
                flexible_seats=table_config.flexible_seats,
                room_rules=config.rules,
                allow_cbc=default.allow_cbc,
                card_prizes=default.card_prizes,
                allow_wagers=default.allow_wagers,
                allow_departures=default.departures.enabled,
                spectator=default.spectators.fresh(),
            )
            tables.append(table)
            if index == 0:
                bindings.append((config, table))
    host.ordinary_tables = tuple(tables)
    host.ordinary_table_routes = {service_nick(t.table_raw): t for t in tables}
    host.ordinary_room_bindings = tuple(bindings)
    host.ordinary_room_routes = {
        service_nick(t.room.room_id ^ GAME_KEY): t for t in (default, *(t for _, t in bindings))
    }


def directory_files(host):
    files = host.event_rooms.directory_files()
    rooms = bytearray(files["b/g/RL000"])
    zones = bytearray(files["b/g/ZL"])
    start = struct.unpack_from("<I", rooms, 0x40)[0]
    struct.pack_into("<I", rooms, 0x40, start + len(host.ordinary_room_configs))
    for row, (config, table) in enumerate(host.ordinary_room_bindings, start):
        rooms[0x48 + row * 0xC8 : 0x48 + (row + 1) * 0xC8] = build_room_record(
            identity=table.room.room_id,
            name=config.name + b"*",
            channel=config.room_channel,
            member_count=len(table.room.members),
            occupied_tables=sum(t.table_count > 0 for t in table.room.tables),
            table_count=len(table.room.tables),
            fixed_rules=config.rules is not None,
            computer_enabled=any(t.computer_enabled for t in table.room.tables),
            rules=(config.rules or RoomRules()).packed(),
        )
    struct.pack_into("<I", rooms, 0x48 + 0x10, len(host.room.members))
    struct.pack_into("<I", rooms, 0x48 + 0x18, int(host.room.table_count > 0))
    rooms[0x48 + 0x29] = ord("A") if host.room.computer_enabled else 0
    struct.pack_into(
        "<II",
        zones,
        0x48,
        sum(len(t.room.members) for t in host.ordinary_room_routes.values())
        + sum(len(b.room.members) for b in host.event_rooms.rooms if b.zone_index == 0),
        start + len(host.ordinary_room_configs),
    )
    files["b/g/ZL"] = bytes(zones)
    files["b/g/RL000"] = bytes(rooms)
    return files
