"""Explicit ordinary rooms and prepublished independent tables per room."""

import json
import re
import struct
from dataclasses import dataclass
from pathlib import Path

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
from tmserver.rooms.packets import ROOM_CHANNEL, TABLE_CHANNEL, TRADE_CHANNEL, alphahex
from tmserver.rooms.service import RoomService
from tmserver.rooms.rules import RoomRules
from tmserver.rooms.spectators import SpectatorService


@dataclass(frozen=True)
class OrdinaryTableConfig:
    table_raw: int
    table_channel: bytes
    mode: str = "versus"
    players: int = 2
    flexible_seats: bool = False

    def __post_init__(self):
        if type(self.table_raw) is not int or not 0 <= self.table_raw <= 0x1FFFFFFFFFF:
            raise ValueError("ordinary table route must be a raw41-bit identity")
        if (
            self.mode not in ("computer", "versus")
            or type(self.players) is not int
            or self.players not in (2, 3)
        ):
            raise ValueError("invalid ordinary table mode or seat count")
        if self.mode == "computer" and self.players != 2:
            raise ValueError("computer tables require two seats")
        if (
            type(self.flexible_seats) is not bool
            or self.flexible_seats
            and (self.mode != "versus" or self.players != 3)
        ):
            raise ValueError("automatic seats require a three-seat versus table")
        if not isinstance(self.table_channel, bytes) or not re.fullmatch(
            rb"#[A-Za-z0-9_-]{1,14}", self.table_channel
        ):
            raise ValueError("table channel must fit its terminated sixteen-byte field")


@dataclass(frozen=True)
class OrdinaryRoomConfig:
    name: bytes
    room_raw: int
    table_raw: int
    room_channel: bytes
    table_channel: bytes
    mode: str = "versus"
    players: int = 2
    tables: tuple[OrdinaryTableConfig, ...] = ()
    flexible_seats: bool = False
    rules: RoomRules | None = None

    def __post_init__(self):
        if self.rules is not None and not isinstance(self.rules, RoomRules):
            raise ValueError("room rules require a RoomRules value")
        if (
            not isinstance(self.name, bytes)
            or not re.fullmatch(rb"[ -~]{1,30}", self.name)
            or b"*" in self.name
        ):
            raise ValueError("room name must fit thirty printable bytes without asterisk")
        for raw in (self.room_raw, self.table_raw):
            if type(raw) is not int or not 0 <= raw <= 0x1FFFFFFFFFF:
                raise ValueError("ordinary room routes must be raw41-bit identities")
        if (
            self.mode not in ("computer", "versus")
            or type(self.players) is not int
            or self.players not in (2, 3)
        ):
            raise ValueError("invalid ordinary room match mode or seat count")
        if self.mode == "computer" and self.players != 2:
            raise ValueError("computer rooms require two seats")
        if (
            type(self.flexible_seats) is not bool
            or self.flexible_seats
            and (self.mode != "versus" or self.players != 3)
        ):
            raise ValueError("automatic seats require a three-seat versus room")
        if not isinstance(self.tables, tuple) or any(
            not isinstance(t, OrdinaryTableConfig) for t in self.tables
        ):
            raise ValueError("additional room tables must be a tuple of table configurations")
        if (
            self.rules is not None
            and self.rules.gs != 0
            and any(t.mode == "computer" for t in (self, *self.tables))
        ):
            # The hosted Computer constructor sets gs=0, and its menu has no
            # departure-rule control. ComGameInit carries no override for it.
            raise ValueError("fixed rooms with Computer tables require departure rule gs=0")
        if len(self.tables) > 126:
            raise ValueError("room tables and trade service exceed native128-slot limit")
        RoomService(
            self.players,
            room_id=game_id(self.room_raw),
            table_id=game_id(self.table_raw),
            room_channel=self.room_channel,
            table_channel=self.table_channel,
        )


def load_ordinary_rooms(path):
    document = json.loads(Path(path).read_text(encoding="utf8"))
    if (
        not isinstance(document, dict)
        or set(document) != {"rooms"}
        or not isinstance(document["rooms"], list)
    ):
        raise ValueError("ordinary configuration requires a rooms array")
    rows = []
    required = {"name", "room_raw", "table_raw", "room_channel", "table_channel"}
    for row in document["rooms"]:
        if (
            not isinstance(row, dict)
            or not required <= set(row)
            or set(row) - required - {"mode", "players", "tables", "rules"}
        ):
            raise ValueError("missing or unknown ordinary room configuration field")
        row = dict(row)
        if "rules" in row:
            if not isinstance(row["rules"], dict):
                raise ValueError("room rules must be an object")
            try:
                row["rules"] = RoomRules(**row["rules"])
            except TypeError as exc:
                raise ValueError("unknown room rule field") from exc
        if row.get("players") == "auto":
            row.update(players=3, flexible_seats=True)
        extra = row.get("tables", [])
        if not isinstance(extra, list):
            raise ValueError("additional room tables require an array")
        tables = []
        for table in extra:
            if (
                not isinstance(table, dict)
                or not {"table_raw", "table_channel"} <= set(table)
                or set(table) - {"table_raw", "table_channel", "mode", "players"}
            ):
                raise ValueError("missing or unknown additional table configuration field")
            table = dict(table)
            if table.get("players") == "auto":
                table.update(players=3, flexible_seats=True)
            if not isinstance(table["table_channel"], str):
                raise ValueError("table channel must be a string")
            table["table_channel"] = table["table_channel"].encode("ascii")
            tables.append(OrdinaryTableConfig(**table))
        row["tables"] = tuple(tables)
        for key in ("name", "room_channel", "table_channel"):
            if not isinstance(row[key], str):
                raise ValueError("room text must be a string")
            row[key] = row[key].encode("ascii")
        rows.append(OrdinaryRoomConfig(**row))
    return tuple(rows)


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
    initial = host.event_rooms.directory_files()["b/g/RL000"]
    if struct.unpack_from("<I", initial, 0x40)[0] + len(configs) > 256:
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
            template = default.spectators
            observer = SpectatorService(
                enabled=template.enabled,
                clock=template.clock,
                bootstrap_delay=template.bootstrap_delay,
                turn_delay=template.turn_delay,
                action_delay=template.action_delay,
                packet_delay=template.packet_delay,
                max_backlog=template.max_backlog,
                max_observers=template.max_observers,
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
                spectator=observer,
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
        record = memoryview(rooms)[0x48 + row * 0xC8 : 0x48 + (row + 1) * 0xC8]
        struct.pack_into("<Q", record, 0, table.room.room_id)
        struct.pack_into("<I", record, 0x10, len(table.room.members))
        struct.pack_into("<I", record, 0x18, sum(t.table_count > 0 for t in table.room.tables))
        struct.pack_into("<H", record, 0x24, 1)
        record[0x27] = 1
        record[0x28] = ord("A" if config.rules is not None else "B")
        record[0x29] = ord("A") if any(t.computer_enabled for t in table.room.tables) else 0
        name = config.name + b"*"
        record[0x2A : 0x2A + len(name)] = name
        record[0x49:0x50] = (config.rules or RoomRules()).packed()
        record[0x6A:0x6D] = alphahex(len(table.room.tables), 3)
        record[0x6D:0x70] = alphahex(16, 3)
        record[0x70] = ord("A")
        record[0xB8 : 0xB8 + len(config.room_channel)] = config.room_channel
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
