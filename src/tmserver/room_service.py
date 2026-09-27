"""One local room with coherent PTL snapshots and sequenced roster updates.

Layouts and ordering: protocol_map/ROOM_SEQUENCE.md, ROOM_CONTRACT_VECTORS.md,
DIRECTORY_UI_CONTRACT.md. Allocation and capacity are synthetic local policy.
"""

import re
import struct
from collections import OrderedDict

from tmserver.identities import ROOM_RAW, TABLE_RAW, TRADE_RAW, game_id
from tmserver.match_service import DEFAULT_GAME_RULES, DEFAULT_TABLE_RULES
from tmserver.table_metadata import table_info as encode_table_info
from tmserver.tm_protocol import Command, encode, parse

ROOM_ID = game_id(ROOM_RAW)
TABLE_ID = game_id(TABLE_RAW)
ROOM_CHANNEL = b"#LOCALROOM01"
TABLE_CHANNEL = b"#LOCALTABLE1"
TRADE_CHANNEL = b"#LOCALTRADE1"


def alphahex(value, width):
    if not 0 <= value < 16**width:
        raise ValueError("metadata value does not fit")
    return bytes(65 + int(x, 16) for x in f"{value:0{width}X}")


def table_info(number=1, count=0, owner=0, game_rules=None, table_rules=None):
    return encode_table_info(
        number,
        count,
        owner,
        DEFAULT_GAME_RULES if game_rules is None else game_rules,
        DEFAULT_TABLE_RULES if table_rules is None else table_rules,
    )


def directory_files():
    zones = bytearray(0x848)
    struct.pack_into("<I", zones, 0x40, 1)
    struct.pack_into("<II", zones, 0x48, 0, 1)
    zones[0x54:0x59] = b"ENAAA"
    zones[0x59:0x65] = b"Local Lobby\0"
    zones[0x74:0x7E] = b"127.0.0.1\0"
    rooms = bytearray(0xC848)
    struct.pack_into("<I", rooms, 0x40, 1)
    r = memoryview(rooms)[0x48:0x110]
    struct.pack_into("<Q", r, 0, ROOM_ID)
    struct.pack_into("<H", r, 0x24, 1)
    r[0x27] = 1
    r[0x28] = ord("A")
    r[0x2A:0x35] = b"Local Room*"
    r[0x49:0x50] = b"AAAAADA"  # wager0, flags0, time3, quit0
    r[0x6A:0x6D] = alphahex(1, 3)
    r[0x6D:0x70] = alphahex(16, 3)
    r[0x70] = ord("A")
    r[0xB8 : 0xB8 + len(ROOM_CHANNEL)] = ROOM_CHANNEL
    return {"b/g/ZL": bytes(zones), "b/g/RL000": bytes(rooms)}


def player_record(payload):
    fields = payload.split(b"\x06")
    if len(fields) != 11:
        raise ValueError("DE/PD requires eleven fields")
    b = bytearray(0x58)
    for index, offset, fmt, base in [
        (0, 0, "Q", 16),
        (1, 0x18, "I", 10),
        (2, 0x26, "B", 10),
        (3, 0x24, "H", 10),
        (5, 0x27, "B", 10),
        (7, 0x1C, "I", 10),
        (8, 0x20, "I", 10),
        (9, 8, "Q", 16),
    ]:
        try:
            struct.pack_into("<" + fmt, b, offset, int(fields[index], base))
        except (ValueError, struct.error) as exc:
            raise ValueError("invalid player field") from exc
    for index, offset, size in [(4, 0x28, 16), (10, 0x38, 32)]:
        if len(fields[index]) > size or b"\0" in fields[index]:
            raise ValueError("player string exceeds record")
        b[offset : offset + len(fields[index])] = fields[index]
    return struct.unpack_from("<Q", b)[0], bytes(b)


class RoomService:
    def __init__(
        self,
        player_count=2,
        *,
        room_id=ROOM_ID,
        room_channel=ROOM_CHANNEL,
        table_id=TABLE_ID,
        table_channel=TABLE_CHANNEL,
        service_records=None,
    ):
        if type(player_count) is not int or player_count not in (2, 3):
            raise ValueError("table player count must be two or three")
        if type(room_id) is not int or not 0 < room_id < 1 << 64:
            raise ValueError("invalid room identity")
        if not isinstance(room_channel, bytes) or not re.fullmatch(
            rb"#[A-Za-z0-9_-]{1,11}", room_channel
        ):
            raise ValueError("room channel must fit the original twelve-byte name")
        if table_id is not None and (type(table_id) is not int or not 0 < table_id < 1 << 64):
            raise ValueError("invalid table identity")
        if table_id is not None and (
            not isinstance(table_channel, bytes)
            or not re.fullmatch(rb"#[A-Za-z0-9_-]{1,14}", table_channel)
        ):
            raise ValueError("table channel must fit its terminated sixteen-byte field")
        if service_records is None:
            # Original100156E5 selects the last D-prefix PTL service record.
            trade = bytearray(0x68)
            struct.pack_into("<Q", trade, 0, game_id(TRADE_RAW))
            trade[0x18 : 0x18 + len(TRADE_CHANNEL)] = TRADE_CHANNEL
            trade[0x28:0x59] = b"D" + b"A" * 48
            service_records = (bytes(trade),)
        records = tuple(service_records)
        if len(records) + int(table_id is not None) > 128:
            raise ValueError("PTL exceeds the original live table array")
        identities = {room_id}
        channels = {room_channel.upper()}
        if table_id is not None:
            if table_id in identities or table_channel.upper() in channels:
                raise ValueError("room and table routes overlap")
            identities.add(table_id)
            channels.add(table_channel.upper())
        for record in records:
            if not isinstance(record, bytes) or len(record) != 0x68:
                raise ValueError("service record must be exactly104 bytes")
            identity = struct.unpack_from("<Q", record)[0]
            field = record[0x18:0x28]
            channel = field.split(b"\0", 1)[0]
            if not identity or identity in identities or channel.upper() in channels:
                raise ValueError("service record routes overlap")
            if (
                b"\0" not in field
                or not re.fullmatch(rb"#[A-Za-z0-9_-]{1,14}", channel)
                or not record[0x28]
            ):
                raise ValueError("service record needs a terminated channel and metadata")
            identities.add(identity)
            channels.add(channel.upper())
        self.player_count = player_count
        self.room_id, self.room_channel = room_id, room_channel
        self.table_id, self.table_channel = table_id, (
            table_channel if table_id is not None else None
        )
        self.service_records = records
        self.sequence = 100
        self.members = {}
        self.npcs = {}
        self.history = OrderedDict()
        self.table_state = 0
        self.table_count = 0
        self.table_owner = 0
        self.table_metadata = table_info()
        self.table_number = 1
        self.room_root = self
        self.tables = [self]
        self.npc_tables = {}

    def add_table(self, table_id, table_channel, player_count=2):
        """Prepublish another independent table under this room's one sequence."""
        if self.table_id is None:
            raise ValueError("ordinary tables need an ordinary parent room")
        if not isinstance(table_channel, bytes):
            raise ValueError("table channel must be bytes")
        routes = [(t.table_id, t.table_channel) for t in self.tables]
        routes += [
            (struct.unpack_from("<Q", r)[0], r[0x18:0x28].split(b"\0")[0])
            for r in self.service_records
        ]
        if len(routes) >= 128:
            raise ValueError("PTL exceeds the original live table array")
        if any(table_id == i or table_channel.upper() == c.upper() for i, c in routes):
            raise ValueError("additional table routes overlap")
        state = RoomService(
            player_count,
            room_id=self.room_id,
            room_channel=self.room_channel,
            table_id=table_id,
            table_channel=table_channel,
            service_records=(),
        )
        state.table_number = len(self.tables) + 1
        state.table_metadata = table_info(number=state.table_number)
        view = RoomTableView(self, state)
        self.tables.append(view)
        return view

    def table_record(self):
        if self.table_id is None:
            raise ValueError("this room has no ordinary playable table")
        b = bytearray(0x68)
        struct.pack_into("<QIIIB", b, 0, self.table_id, 0, 0, 0, self.table_state)
        b[0x18 : 0x18 + len(self.table_channel)] = self.table_channel
        metadata = self.table_metadata
        b[0x28 : 0x28 + len(metadata)] = metadata
        return bytes(b)

    def snapshot(self):
        b = bytearray(0xC050)
        records = [*self.members.values(), *self.npcs.values()]
        tables = (
            tuple(t.table_record() for t in self.tables if t.table_id is not None)
            + self.service_records
        )
        struct.pack_into("<III", b, 0x40, self.sequence, len(records), len(tables))
        for i, (_, record) in enumerate(records):
            b[0x50 + 0x58 * i : 0xA8 + 0x58 * i] = record
        for i, record in enumerate(tables):
            start = 0x5850 + 0x68 * i
            b[start : start + 0x68] = record
        return bytes(b)

    def set_table(
        self, state, count, owner, game_rules=None, table_rules=None, *, computer_menu=False
    ):
        return self._set_table(
            self, state, count, owner, game_rules, table_rules, computer_menu=computer_menu
        )

    def _set_table(
        self, slot, state, count, owner, game_rules=None, table_rules=None, *, computer_menu=False
    ):
        if slot.table_id is None:
            raise ValueError("this room has no ordinary playable table")
        # The native Computer menu chooses two or three seats after admission.
        capacity = 3 if computer_menu else slot.player_count
        if not 0 <= state <= 6 or not 0 <= count <= capacity or not 0 <= owner < 1 << 64:
            raise ValueError("invalid table state")
        metadata = table_info(
            number=slot.table_number,
            count=count,
            owner=owner,
            game_rules=game_rules,
            table_rules=table_rules,
        )
        if (state, count, owner, metadata) == (
            slot.table_state,
            slot.table_count,
            slot.table_owner,
            slot.table_metadata,
        ):
            return []
        slot.table_state, slot.table_count, slot.table_owner = state, count, owner
        slot.table_metadata = metadata
        fields = (
            slot.table_channel,
            b"0",
            f"0x{slot.table_id:016X}".encode(),
            str(state).encode(),
            b"0",
            b"0",
            metadata,
        )
        update = self.delta(Command(b"TD", b"\x06".join(fields)))
        return [(peer, update) for peer in self.members]

    def set_npc(self, payload, *, table_id=None):
        if table_id is None:
            table_id = self.table_id
        identity, record = player_record(payload)
        if identity in self.npcs and self.npc_tables[identity] != table_id:
            raise ValueError("practice opponent identity overlaps another table")
        if identity not in self.npcs and table_id in self.npc_tables.values():
            raise ValueError("only one practice opponent is supported")
        return self._publish_npc(payload, identity, record, table_id)

    def _publish_npc(self, payload, identity, record, table_id):
        if self.npcs.get(identity) == (identity, record):
            return []
        self.npcs[identity] = identity, record
        self.npc_tables[identity] = table_id
        update = self.delta(Command(b"PD", payload))
        return [(peer, update) for peer in self.members]

    def practice_opponent(self, peer, opponent, *, table_id=None):
        if peer not in self.members:
            return []
        return self.set_npc(self._opponent_payload(peer, opponent), table_id=table_id)

    def practice_opponents(self, peer, opponents, *, table_id=None):
        """Synchronize a Computer menu's one or two NPCs in the shared roster."""
        if peer not in self.members:
            return []
        if table_id is None:
            table_id = self.table_id
        opponents = tuple(opponents)
        if len(opponents) > 2:
            raise ValueError("Computer menu supports at most two opponents")
        records = {}
        members = {identity for identity, _ in self.members.values()}
        for opponent in opponents:
            payload = self._opponent_payload(peer, opponent)
            identity, record = player_record(payload)
            if (
                identity in records
                or identity in members
                or identity in self.npcs and self.npc_tables[identity] != table_id
            ):
                raise ValueError("practice opponent identity is already present")
            records[identity] = payload, record
        # Validate the complete replacement before removing an old second NPC.
        deliveries = []
        for identity in list(self.npcs):
            if self.npc_tables[identity] == table_id and identity not in records:
                deliveries.extend(self._remove_npc(identity))
        for identity, (payload, record) in records.items():
            deliveries.extend(self._publish_npc(payload, identity, record, table_id))
        return deliveries

    def _opponent_payload(self, peer, opponent):
        # Original10066950 projects info[23:25] to status/substatus. The
        # table Start gate requires every roster member to exist here as3/1.
        info = bytearray(self.members[peer][1][0x38:0x58].rstrip(b"\0").ljust(32, b"A"))
        name = opponent.name[:15]  # Native CPU menu names may exceed the room name field.
        info[:15] = name.ljust(15, b" ")
        info[15] = ord("A") + len(name)
        info[23:25] = b"DB"
        fields = (
            f"0x{opponent.nickname_id:016X}".encode(),
            str(opponent.character_id).encode(),
            b"0",
            b"0",
            name,
            b"2",
            b"0",
            b"0",
            b"0",
            f"0x{self.room_id:016X}".encode(),
            bytes(info),
        )
        return b"\x06".join(fields)

    def _remove_npc(self, identity):
        del self.npcs[identity]
        del self.npc_tables[identity]
        update = self.delta(Command(b"PC", f"0x{identity:016X}".encode()))
        return [(peer, update) for peer in self.members]

    def clear_npcs(self, *, table_id=None):
        if table_id is None:
            table_id = self.table_id
        deliveries = []
        for identity in list(self.npcs):
            if self.npc_tables[identity] != table_id:
                continue
            deliveries.extend(self._remove_npc(identity))
        return deliveries

    def delta(self, command):
        self.sequence += 1
        self.history[self.sequence] = command
        while len(self.history) > 256:
            self.history.popitem(last=False)
        return self.wrap_delta(self.sequence, command)

    @staticmethod
    def wrap_delta(sequence, command):
        return b"GTM0L" + encode(
            [
                Command(b"DD", b"0"),
                Command(b"DN", b"1"),
                Command(b"DC", str(sequence).encode()),
                command,
            ]
        )

    def handle(self, peer, payload, *, authenticated_identity=None):
        if not payload.startswith(b"GTM0L"):
            return None
        commands = parse(payload[5:])
        if len(commands) != 1:
            return None
        c = commands[0]
        if c.token in (b"DE", b"PD"):
            identity, record = player_record(c.payload)
            if authenticated_identity is not None:
                nickname, character = authenticated_identity
                if identity != nickname or struct.unpack_from("<I", record, 0x18)[0] != character:
                    raise ValueError("room presence differs from authenticated account")
                if struct.unpack_from("<Q", record, 8)[0] != self.room_id:
                    raise ValueError("room presence names a different room")
            if (
                any(other is not peer and old[0] == identity for other, old in self.members.items())
                or identity in self.npcs
            ):
                raise ValueError("room identity is already present")
            if c.token == b"PD" and peer not in self.members:
                raise ValueError("presence update requires room admission")
            if peer in self.members and self.members[peer][0] != identity:
                raise ValueError("room identity cannot change during admission")
            if peer not in self.members and len(self.members) >= 16:
                return [(peer, b"GTM0L" + encode([Command(b"DF", b"-1")]))]
            self.members[peer] = identity, record
            update = self.delta(Command(b"PD", c.payload))
            deliveries = (
                [(peer, b"GTM0L" + encode([Command(b"DS", f"0x{self.room_id:016X}".encode())]))]
                if c.token == b"DE"
                else []
            )
            deliveries.extend((member, update) for member in self.members)
            return deliveries
        if c.token == b"PC":
            if not re.fullmatch(rb"0x[0-9A-Fa-f]{16}", c.payload):
                raise ValueError("room departure requires the original uint64 identity")
            identity = int(c.payload, 16)
            expected = (
                authenticated_identity[0]
                if authenticated_identity is not None
                else self.members.get(peer, (None,))[0]
            )
            if expected is not None and identity != expected:
                raise ValueError("cannot remove another room member")
            return self.leave(peer)
        if c.token == b"DR":
            sequence = int(c.payload)
            if sequence == self.sequence:
                return []
            if sequence > self.sequence or sequence < self.sequence - len(self.history):
                return [(peer, b"GTM0L" + encode([Command(b"DO", b"")]))]
            return [
                (peer, self.wrap_delta(s, cmd)) for s, cmd in self.history.items() if s > sequence
            ]
        return None

    def leave(self, peer):
        old = self.members.pop(peer, None)
        if old is None:
            return []
        packet = self.delta(Command(b"PC", f"0x{old[0]:016X}".encode()))
        return [(member, packet) for member in self.members]


class RoomTableView:
    """Per-table metadata and NPC cleanup over a shared room roster/replay."""

    _SLOT = frozenset(
        (
            "table_id",
            "table_channel",
            "table_number",
            "player_count",
            "table_state",
            "table_count",
            "table_owner",
            "table_metadata",
            "table_record",
        )
    )
    _ROOM = frozenset(
        (
            "room_id",
            "room_channel",
            "members",
            "npcs",
            "sequence",
            "history",
            "snapshot",
            "handle",
            "leave",
            "delta",
            "wrap_delta",
            "service_records",
            "tables",
        )
    )

    def __init__(self, root, state):
        self.room_root = root
        self._state = state

    def __getattr__(self, name):
        if name in self._SLOT:
            return getattr(self._state, name)
        if name in self._ROOM:
            return getattr(self.room_root, name)
        raise AttributeError(name)

    def set_table(
        self, state, count, owner, game_rules=None, table_rules=None, *, computer_menu=False
    ):
        return self.room_root._set_table(
            self._state,
            state,
            count,
            owner,
            game_rules,
            table_rules,
            computer_menu=computer_menu,
        )

    def practice_opponent(self, peer, opponent):
        return self.room_root.practice_opponent(peer, opponent, table_id=self.table_id)

    def practice_opponents(self, peer, opponents):
        return self.room_root.practice_opponents(peer, opponents, table_id=self.table_id)

    def clear_npcs(self):
        return self.room_root.clear_npcs(table_id=self.table_id)
