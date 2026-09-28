"""Configured event rooms, exact directory keys, and owner-scoped file routing.

This module does not admit event matches or grant prizes. Call EventHost.admit_room
only after authenticated DE succeeds, persist, then deliver D0 from that room.
"""

import struct
from dataclasses import dataclass

from tmserver.events.service import EventError
from tmserver.protocol.identities import GAME_KEY, ROOM_RAW, SHOP_RAW, TABLE_RAW, TRADE_RAW, game_id
from tmserver.rooms.service import (
    ROOM_CHANNEL,
    TABLE_CHANNEL,
    TRADE_CHANNEL,
    RoomService,
    alphahex,
)
from tmserver.rooms.service import (
    directory_files as base_directory_files,
)
from tmserver.protocol.commands import Command

_DEFAULT_REGION = b"Local Lobby"
_DEFAULT_ROOM = b"Local Room"


def _raw(identity):
    if type(identity) is not int or not 0 < identity < 1 << 64:
        raise EventError("event identity must be a nonzero game-domain qword")
    raw = identity ^ GAME_KEY
    if raw > 0x1FFFFFFFFFF:
        raise EventError("event identity has no routable41-bit POL owner")
    return raw


def _room_channel(raw):
    # Eight base36 digits represent every original41-bit identity; no hash or
    # ordinal, so channels remain stable and unique when configs are reordered.
    alphabet = b"0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    digits = bytearray()
    for _ in range(8):
        raw, digit = divmod(raw, 36)
        digits.append(alphabet[digit])
    assert raw == 0
    return b"#EV" + bytes(reversed(digits))


@dataclass(frozen=True)
class EventRoom:
    event_id: str
    room: RoomService
    raw_owner: int
    zone_index: int

    @property
    def room_id(self):
        return self.room.room_id

    @property
    def room_channel(self):
        return self.room.room_channel


class EventRooms:
    """Route trusted configured events without changing the default lobby.

    room_for_service accepts a game-domain identity, including E/F identities.
    room_for_channel accepts a room/E/F byte channel (IRC case-insensitive).
    profile_file accepts the decoded raw41-bit owner, not its packed directory/
    volume qword. Unsupported owners return None; do not fall back to a different
    room for PTL/EventDataList/EventMemberList.
    """

    def __init__(self, host):
        self.host = host
        configs = tuple(host.configs.values())
        regions = [_DEFAULT_REGION]
        for config in configs:
            region, name = config.listing.key_region, config.listing.key_event
            if not region or len(region) > 26 or b"\0" in region:
                raise EventError("event zone key must fit its terminated27-byte directory field")
            if not name or len(name) > 30 or any(c in name for c in (b"\0", b"*")):
                raise EventError(
                    "event room key must fit before the RL rule overlap without an asterisk"
                )
            if (region, name) == (_DEFAULT_REGION, _DEFAULT_ROOM):
                raise EventError("event key collides with the default lobby room")
            if config.listing.scalar == 0xFFFFFFFF:
                raise EventError("event scalar collides with the original unmatched sentinel")
            if region not in regions:
                regions.append(region)
        if len(regions) > 32:
            raise EventError("event zones exceed the original32-zone file")
        self.regions = tuple(regions)
        self._by_service = {}
        self._by_channel = {}
        self._by_owner = {}
        bindings = []
        used_ids = {game_id(i) for i in (0, ROOM_RAW, TABLE_RAW, SHOP_RAW, TRADE_RAW)}
        used_channels = {c.upper() for c in (ROOM_CHANNEL, TABLE_CHANNEL, TRADE_CHANNEL)}
        for config in configs:
            raw = _raw(config.room_id)
            channel = _room_channel(raw)
            ids = (config.room_id, config.init_id, config.card_id)
            channels = (channel, config.init_channel, config.card_channel)
            for identity in ids:
                _raw(identity)
                if identity in used_ids:
                    raise EventError("event identity overlaps another local route")
                used_ids.add(identity)
            for value in channels:
                key = value.upper()
                if key in used_channels:
                    raise EventError("event channel overlaps another local route")
                used_channels.add(key)
            room = RoomService(
                room_id=config.room_id,
                room_channel=channel,
                table_id=None,
                service_records=config.service_records(),
            )
            binding = EventRoom(config.id, room, raw, regions.index(config.listing.key_region))
            bindings.append(binding)
            self._by_owner[raw] = binding
            for identity in ids:
                self._by_service[identity] = binding
            for value in channels:
                self._by_channel[value.upper()] = binding
        self.rooms = tuple(bindings)

    def room_for_service(self, identity):
        return self._by_service.get(identity)

    def room_for_channel(self, channel):
        return self._by_channel.get(channel.upper()) if isinstance(channel, bytes) else None

    def bind_match_table(self, event_id, table_id, table_channel, player_count=2):
        """Bind an explicit table and return initial TD updates before offers.

        Validation is complete before mutation. Repeated identical binding is
        idempotent. This does not enable E discovery or create an event match.
        """
        binding = next((b for b in self.rooms if b.event_id == event_id), None)
        if binding is None:
            raise EventError("unknown event room")
        room = binding.room
        _raw(table_id)  # Validate the service identity before binding it.
        for existing in room.tables:
            if existing.table_id == table_id:
                if (existing.table_channel, existing.player_count) == (table_channel, player_count):
                    return []
                raise EventError("event table is already bound with different settings")
        if table_id in self._by_service or table_id in {
            game_id(i) for i in (0, ROOM_RAW, TABLE_RAW, SHOP_RAW, TRADE_RAW)
        }:
            raise EventError("event table identity overlaps a route")
        if (
            not isinstance(table_channel, bytes)
            or table_channel.upper() in self._by_channel
            or table_channel.upper() in {ROOM_CHANNEL, TABLE_CHANNEL, TRADE_CHANNEL}
        ):
            raise EventError("event table channel overlaps a route")
        # Reuse all original record/channel/capacity validation before assignment.
        if room.table_id is None:
            checked = RoomService(
                player_count,
                room_id=room.room_id,
                room_channel=room.room_channel,
                table_id=table_id,
                table_channel=table_channel,
                service_records=room.service_records,
            )
            room.table_id, room.table_channel, room.player_count = (
                checked.table_id,
                checked.table_channel,
                checked.player_count,
            )
            slot = room
        else:
            slot = room.add_table(table_id, table_channel, player_count)
        self._by_service[table_id] = binding
        self._by_channel[table_channel.upper()] = binding
        fields = (
            table_channel,
            b"0",
            f"0x{table_id:016X}".encode(),
            b"0",
            b"0",
            b"0",
            slot.table_metadata,
        )
        update = room.delta(Command(b"TD", b"\x06".join(fields)))
        return [(peer, update) for peer in room.members]

    def directory_files(self):
        files = base_directory_files()
        zones = bytearray(files["b/g/ZL"])
        struct.pack_into("<I", zones, 0x40, len(self.regions))
        for index, region in enumerate(self.regions):
            members = [binding for binding in self.rooms if binding.zone_index == index]
            if index == 0:
                rooms = bytearray(files["b/g/RL000"])
                start = 1
            else:
                rooms = bytearray(0xC848)
                start = 0
            count = start + len(members)
            if count > 256:
                raise EventError("event room directory exceeds its array")
            struct.pack_into("<I", rooms, 0x40, count)
            for row, binding in enumerate(members, start):
                config = self.host.config(binding.event_id)
                record = memoryview(rooms)[0x48 + row * 0xC8 : 0x48 + (row + 1) * 0xC8]
                struct.pack_into("<Q", record, 0, binding.room_id)
                struct.pack_into("<I", record, 0x10, len(binding.room.members))
                struct.pack_into(
                    "<I", record, 0x18, sum(t.table_count > 0 for t in binding.room.tables)
                )
                struct.pack_into("<H", record, 0x24, 1)
                record[0x27] = 1
                # B is the proven "Not Set" rule-panel path. Event association
                # is the exact names lookup, independent of the A/B flag.
                record[0x28] = ord("B")
                name = config.listing.key_event + b"*"
                record[0x2A : 0x2A + len(name)] = name
                record[0x6A:0x6D] = alphahex(
                    sum(t.table_id is not None for t in binding.room.tables), 3
                )
                record[0x6D:0x70] = alphahex(16, 3)
                record[0x70] = ord("A")
                record[0xB8 : 0xB8 + len(binding.room_channel)] = binding.room_channel
            files[f"b/g/RL{index:03d}"] = bytes(rooms)
            record = memoryview(zones)[0x48 + index * 0x40 : 0x48 + (index + 1) * 0x40]
            struct.pack_into("<II", record, 0, sum(len(b.room.members) for b in members), count)
            record[0x0C:0x11] = b"ENAAA"
            record[0x11:0x2C] = region.ljust(27, b"\0")
            record[0x2C:0x3C] = b"127.0.0.1\0".ljust(16, b"\0")
            record[0x3C] = index
        files["b/g/ZL"] = bytes(zones)
        return files

    def profile_file(self, raw_owner, path):
        if path == "b/g/TM0EventList":
            return self.host.file(path)
        if path == "b/g/ZL" or path.startswith("b/g/RL"):
            return self.directory_files().get(path)
        if type(raw_owner) is not int or not 0 <= raw_owner <= 0x1FFFFFFFFFF:
            return None
        binding = self._by_owner.get(raw_owner)
        if binding is None:
            return None
        if path == "b/g/PTL":
            return binding.room.snapshot()
        if path in ("b/g/TM0EventDataList", "b/g/TM0EventMemberList"):
            return self.host.file(path, binding.event_id)
        return None

    def disconnect(self, peer, account_key):
        """Return (binding, [(recipient, DD/PC), ...]) groups; no I/O/persistence."""
        groups = []
        for binding in self.rooms:
            if peer in binding.room.members:
                groups.append((binding, binding.room.leave(peer)))
        self.host.disconnect(account_key)
        return groups
