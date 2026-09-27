"""Read-only local ranking requests and original PC binary result files.

Wire: GTM0R RR(category),PI(owner,directory,version,selector) -> RF,LN / RG.
Files: E8-byte ranking rows; b/g/TM0RkData is seven little-endian DWORDs.
Sources: ../protocol_map/{AUCTION_RANK_CONTRACT,FILE_COVERAGE,RESULT_REWARD_SEQUENCE}.

Local policy: committed synthetic accounts; score descending with stable
identity/key ties. Current rating and total prize money come from canonical
profile fields30/50. Historical categories stay empty until explicit history
is supplied. No historical aggregation, eligibility, reset or award is invented.
"""

import re
import struct
from dataclasses import dataclass, replace
from types import MappingProxyType

from tmserver.tm_protocol import Command, encode, parse

RECORD_SIZE = 0xE8
METADATA_PATH = "b/g/TM0RkData"
RESULT_PATH = "U/g/TM0_RANKLIST"
RANK_IDS = (0, 1, 2, 3, 4)
SCORE_OFFSETS = {0: 0x94, 1: 0x9C, 2: 0xA0, 3: 0xA4, 4: 0xA4}
PREVIOUS_OFFSETS = {0: 0xC6, 1: 0xC7, 2: 0xC5, 3: 0xC4, 4: 0xC3}
UI_CATEGORIES = {0: 4, 1: 5, 2: 0, 3: 1, 4: 2}


class RankingError(ValueError):
    """Malformed or unauthorized local request; no partial snapshot is stored."""


def _uint(value, bits, name):
    if type(value) is not int or not 0 <= value < (1 << bits):
        raise RankingError(f"{name} must be uint{bits}")
    return value


@dataclass(frozen=True)
class RankingHistory:
    """Optional explicit data, not computed from current profile by this module.

    Previous positions are in RR order0..4. Zero means unavailable. Positive
    values1..127 are the original signed-byte representable display range.
    """

    weekly_prize: int | None = None
    last_week_rating: int | None = None
    rookie: bool = False
    anonymous: bool = False
    qualified: bool = True
    previous_ranks: tuple[int, ...] = (0, 0, 0, 0, 0)

    def __post_init__(self):
        for name in ("weekly_prize", "last_week_rating"):
            value = getattr(self, name)
            if value is not None:
                _uint(value, 32, name)
        for name in ("rookie", "anonymous", "qualified"):
            if type(getattr(self, name)) is not bool:
                raise RankingError(f"{name} must be boolean")
        if (
            not isinstance(self.previous_ranks, tuple)
            or len(self.previous_ranks) != 5
            or any(type(value) is not int or not 0 <= value <= 127 for value in self.previous_ranks)
        ):
            raise RankingError("previous_ranks requires five integers0..127 in RR order")


@dataclass(frozen=True)
class _Entry:
    key: str
    identity: int
    character_id: int
    name: bytes
    rating: int
    total_prize: int
    history: RankingHistory

    def score(self, rank_id):
        return (
            self.total_prize,
            self.history.weekly_prize,
            self.rating,
            self.history.last_week_rating,
            self.history.last_week_rating,
        )[rank_id]


def encode_record(entry):
    """Only fields consumed by the audited rank UI are populated; rest zero."""
    output = bytearray(RECORD_SIZE)
    # Original101752F7 copies until NUL, even though the source name field is16.
    # Keep a guaranteed terminator; this display-only form does not rename saves.
    output[:16] = entry.name[:15].ljust(16, b"\0")
    struct.pack_into("<QI", output, 0x10, entry.identity, entry.character_id)
    struct.pack_into("<I", output, 0x94, entry.total_prize)
    struct.pack_into("<I", output, 0x9C, entry.history.weekly_prize or 0)
    struct.pack_into("<I", output, 0xA0, entry.rating)
    struct.pack_into("<I", output, 0xA4, entry.history.last_week_rating or 0)
    output[0xC0] = int(entry.history.anonymous)
    output[0xC2] = int(entry.history.qualified)
    for rank_id, offset in PREVIOUS_OFFSETS.items():
        output[offset] = entry.history.previous_ranks[rank_id]
    return bytes(output)


class RankingService:
    """Synchronous account-scoped service; no networking or filesystem writes.

    handle(key,payload) -> [reply] or None for another message family.
    file(key,path) -> bytes or None. Caller applies ordinary file offset/maximum.
    delete(key,path) -> bool. True also means idempotent recognized cleanup.

    Each RF gets a unique immutable filename. Exact deletion removes that file;
    canonical U/g/TM0_RANKLIST deletion clears the account's rank snapshots,
    matching the original UI's fixed-name cleanup on entry. Cache exhaustion
    returns RG(-8809), never silently evicts an unread result.
    """

    def __init__(self, economy, *, historical_stats=None, update_time=0, max_snapshots=64):
        self.economy = economy
        self.update_time = _uint(update_time, 32, "update_time")
        if type(max_snapshots) is not int or not 1 <= max_snapshots <= 1024:
            raise RankingError("max_snapshots must be1..1024 per account")
        self.max_snapshots = max_snapshots
        history = dict(historical_stats or {})
        if any(
            not isinstance(key, str) or not isinstance(value, RankingHistory)
            for key, value in history.items()
        ):
            raise RankingError("historical_stats must map account keys to RankingHistory")
        self.history = MappingProxyType(history)
        self._snapshots = {}
        self._next_id = 1

    def _entries(self):
        entries = []
        # snapshot is public and contains only committed accounts. profile_bytes
        # also excludes a staged shop purchase; this module never changes either.
        for key in sorted(self.economy.snapshot()["accounts"]):
            account = self.economy.account_view(key)
            profile = self.economy.profile_bytes(key)
            history = self.history.get(key, RankingHistory())
            history = replace(history, anonymous=history.anonymous or bool(profile[0xB6]))
            entries.append(
                _Entry(
                    key,
                    account.nn,
                    account.player_id,
                    account.name,
                    struct.unpack_from("<I", profile, 0x30)[0],
                    struct.unpack_from("<I", profile, 0x50)[0],
                    history,
                )
            )
        return entries

    @staticmethod
    def _eligible(entries, rank_id):
        if rank_id not in RANK_IDS:
            raise RankingError("unknown ranking ID")
        candidates = [
            entry
            for entry in entries
            if entry.score(rank_id) is not None
            and (rank_id != 2 or entry.history.qualified)
            and (rank_id != 4 or entry.history.rookie)
        ]
        candidates.sort(
            key=lambda entry: (-entry.score(rank_id), entry.identity, entry.character_id, entry.key)
        )
        return candidates

    def records(self, rank_id):
        """Fresh bytes for an explicit category, before request snapshotting."""
        rows = self._eligible(self._entries(), rank_id)
        # Top30 is locally limited to30; all other readers are bounded to100.
        return b"".join(encode_record(row) for row in rows[: 30 if rank_id == 3 else 100])

    def metadata(self):
        entries = self._entries()
        # Offsets4,8,14,18 are display denominators, not record counts. Offset4
        # is shared by Rating and Grand Total, so report all provisioned accounts.
        counts = [
            len(entries),
            len(self._eligible(entries, 1)),
            len(self._eligible(entries, 3)),
            len(self._eligible(entries, 4)),
        ]
        return struct.pack(
            "<7I", 0, counts[0], counts[1], self.update_time, 0, counts[2], counts[3]
        )

    @staticmethod
    def _failure(error):
        return [b"GTM0R" + encode([Command(b"RG", str(error).encode("ascii"))])]

    def handle(self, account_key, payload):
        if not payload.startswith(b"GTM0R"):
            return None
        commands = parse(payload[5:])
        if not commands or commands[0].token != b"RR":
            return None
        if [command.token for command in commands] != [b"RR", b"PI"]:
            raise RankingError("rank request requires RR followed by PI")
        account = self.economy.account_view(account_key)  # Authenticated identity.
        if re.fullmatch(rb"[0-9]{1,10}", commands[0].payload) is None:
            raise RankingError("invalid ranking ID")
        rank_id = int(commands[0].payload)
        fields = commands[1].payload.split(b"\x06")
        if len(fields) != 4 or re.fullmatch(rb"0x[0-9a-fA-F]{16}", fields[0]) is None:
            raise RankingError("PI requires a fixed-width identity and three integers")
        if any(re.fullmatch(rb"[0-9]{1,10}", value) is None for value in fields[1:]):
            raise RankingError("invalid PI integer")
        identity = int(fields[0], 16)
        directory, version, selector = map(int, fields[1:])
        _uint(directory, 8, "directory")
        _uint(version, 16, "version")
        _uint(selector, 32, "selector")
        if identity != account.nn:
            raise RankingError("PI owner does not match authenticated account")
        if selector != 0:
            raise RankingError("nonzero PI selector is not supported by this PC route")
        if rank_id not in RANK_IDS:
            return self._failure(-8811)
        snapshots = self._snapshots.setdefault(account_key, {})
        if len(snapshots) >= self.max_snapshots or self._next_id > 0xFFFFFFFF:
            return self._failure(-8809)
        data = self.records(rank_id)
        path = f"{RESULT_PATH}_{self._next_id:08X}"
        response = b"GTM0R" + encode(
            [
                Command(b"RF", path.encode("ascii")),
                Command(b"LN", str(len(data) // RECORD_SIZE).encode("ascii")),
            ]
        )
        snapshots[path] = data
        self._next_id += 1
        return [response]

    def file(self, account_key, path):
        self.economy.account_view(account_key)
        if path == METADATA_PATH:
            return self.metadata()
        return self._snapshots.get(account_key, {}).get(path)

    def delete(self, account_key, path):
        self.economy.account_view(account_key)
        if path == RESULT_PATH:
            self._snapshots.pop(account_key, None)
            return True
        if re.fullmatch(re.escape(RESULT_PATH) + r"_[0-9A-F]{8}", path) is not None:
            self._snapshots.get(account_key, {}).pop(path, None)
            return True
        return False
