"""Read-only local ranking requests and original PC binary result files.

Wire: GTM0R RR(category),PI(owner,directory,version,selector) -> RF,LN / RG.
Files: E8-byte ranking rows; b/g/TM0RkData is seven little-endian DWORDs.
Sources: ../protocol_map/{AUCTION_RANK_CONTRACT,FILE_COVERAGE,RESULT_REWARD_SEQUENCE}.

Local policy: committed synthetic accounts; score descending unless the
optional local-history rating policy is enabled (lower rating wins). Stable
identity/key ties. Current rating and total prize money come from canonical
profile fields30/50. Historical categories require recorded weekly play or
explicit history. Weekly tracking is opt-in local policy; retail aggregation,
eligibility, reset and award rules remain unknown.
"""

import re
import struct
from dataclasses import dataclass, replace
from types import MappingProxyType

from tmserver.protocol.commands import Command, encode_service, parse
from tmserver.rankings.models import RankingError, RankingHistory, _uint
from tmserver.rankings.packets import (
    METADATA_PATH,
    RECORD_SIZE,
    RESULT_PATH,
    encode_record,
)

RANK_IDS = (0, 1, 2, 3, 4)


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

    def __init__(
        self, economy, *, rating_policy=None, historical_stats=None, update_time=0,
        max_snapshots=64,
    ):
        self.economy = economy
        self.rating_policy = rating_policy
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
        periods = (
            self.rating_policy.weekly_history()[0]
            if self.rating_policy is not None and self.rating_policy.weekly
            else None
        )
        # snapshot is public and contains only committed accounts. profile_bytes
        # also excludes a staged shop purchase; this module never changes either.
        for key in sorted(self.economy.snapshot()["accounts"]):
            account = self.economy.account_view(key)
            profile = self.economy.profile_bytes(key)
            history = self.history.get(key, RankingHistory())
            if periods is not None:
                recorded = periods.get(key, RankingHistory())
                history = replace(
                    history, weekly_prize=recorded.weekly_prize,
                    last_week_rating=recorded.last_week_rating,
                )
            history = replace(history, anonymous=history.anonymous or bool(profile[0xB6]))
            if self.rating_policy is not None and not self.rating_policy.is_ranked(key):
                history = replace(history, qualified=False)
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

    def _eligible(self, entries, rank_id):
        if rank_id not in RANK_IDS:
            raise RankingError("unknown ranking ID")
        candidates = [
            entry
            for entry in entries
            if entry.score(rank_id) is not None
            and (rank_id != 2 or entry.history.qualified)
            and (rank_id != 4 or entry.history.rookie)
        ]
        local_rating = (
            rank_id in (2, 3, 4)
            and self.rating_policy is not None
            and self.rating_policy.enabled
        )
        direction = 1 if local_rating else -1
        candidates.sort(
            key=lambda entry: (
                direction * entry.score(rank_id), entry.identity, entry.character_id, entry.key,
            )
        )
        return candidates

    def records(self, rank_id):
        """Fresh bytes for an explicit category, before request snapshotting."""
        rows = self._eligible(self._entries(), rank_id)
        # Top30 is locally limited to30; all other readers are bounded to100.
        return b"".join(encode_record(row) for row in rows[: 30 if rank_id == 3 else 100])

    def metadata(self):
        entries = self._entries()
        # Native dates share one anchor for a closed tally and the next update.
        # Live weekly totals cannot use it truthfully; zero hides those labels.
        # The policy's saved period start and manually supplied dates stay intact.
        update_time = (
            0
            if self.rating_policy is not None and self.rating_policy.weekly
            else self.update_time
        )
        # Offsets4,8,14,18 are display denominators, not record counts. Offset4
        # is shared by Rating and Grand Total, so report all provisioned accounts.
        counts = [
            len(entries),
            len(self._eligible(entries, 1)),
            len(self._eligible(entries, 3)),
            len(self._eligible(entries, 4)),
        ]
        return struct.pack(
            "<7I", 0, counts[0], counts[1], update_time, 0, counts[2], counts[3]
        )

    @staticmethod
    def _failure(error):
        return [encode_service(b"R", [Command(b"RG", str(error).encode("ascii"))])]

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
        response = encode_service(
            b"R",
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
