"""Ranking requests and the client's binary ranking files.

Wire: GTM0R RR(category), PI(owner, directory, version, selector) -> RF, LN / RG.
Files: E8-byte ranking rows; b/g/TM0RkData is seven little-endian DWORDs. Layout:
TetraPackets irc/R.md and file/ranking-files.md.

Server choice: committed local accounts, ranked by score, or by rating (lower
wins) when the local-history rating policy is on. Ties break by identity and key.
Current rating and total prize money come from profile fields 30 and 50. Weekly
categories need recorded weekly play; Rookies (4) is always empty. The original
server's aggregation, eligibility, reset and award rules are unknown.
"""

import re
import struct
from dataclasses import dataclass

from tmserver.checks import decimal
from tmserver.protocol.commands import Command, encode_service, parse
from tmserver.economy.errors import EconomyError
from tmserver.rankings.packets import METADATA_PATH, RECORD_SIZE, RESULT_PATH, encode_record
from tmserver.rankings.policy import RatingPolicy

RANK_IDS = (0, 1, 2, 3, 4)
MAX_SNAPSHOTS = 64  # Per account; a full cache answers RG(-8809).


@dataclass(frozen=True)
class _Entry:
    key: str
    identity: int
    character_id: int
    name: bytes
    rating: int
    total_prize: int
    weekly_prize: int | None
    last_week_rating: int | None
    anonymous: bool
    qualified: bool

    def score(self, rank_id):
        # Rookies has no rookie rules, so it never has a score.
        return (
            self.total_prize, self.weekly_prize, self.rating, self.last_week_rating, None
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

    def __init__(self, economy, *, rating_policy=None):
        self.economy = economy
        self.rating_policy = rating_policy or RatingPolicy(economy)
        self._snapshots = {}
        self._next_id = 1

    def _entries(self):
        weekly = self.rating_policy.weekly_history()
        entries = []
        # snapshot is public and contains only committed accounts. profile_bytes
        # also excludes a staged shop purchase; this module never changes either.
        for key in sorted(self.economy.snapshot()["accounts"]):
            account = self.economy.account_view(key)
            profile = self.economy.profile_bytes(key)
            entries.append(
                _Entry(
                    key,
                    account.nn,
                    account.player_id,
                    account.name,
                    struct.unpack_from("<I", profile, 0x30)[0],
                    struct.unpack_from("<I", profile, 0x50)[0],
                    *weekly.get(key, (None, None)),
                    bool(profile[0xB6]),
                    self.rating_policy.is_ranked(key),
                )
            )
        return entries

    def _eligible(self, entries, rank_id):
        if rank_id not in RANK_IDS:
            raise EconomyError("unknown ranking ID")
        candidates = [
            entry
            for entry in entries
            if entry.score(rank_id) is not None and (rank_id != 2 or entry.qualified)
        ]
        direction = 1 if rank_id in (2, 3) and self.rating_policy.enabled else -1
        candidates.sort(
            key=lambda entry: (
                direction * entry.score(rank_id), entry.identity, entry.character_id, entry.key,
            )
        )
        return candidates

    def records(self, rank_id):
        """Fresh bytes for an explicit category, before request snapshotting."""
        rows = self._eligible(self._entries(), rank_id)
        # Top30 is locally limited to 30; all other readers are bounded to 100.
        return b"".join(encode_record(row) for row in rows[: 30 if rank_id == 3 else 100])

    def metadata(self):
        entries = self._entries()
        # Offset 12 is the native tally/update date. No server date is truthful
        # for live weekly totals, so zero hides those labels.
        # Offsets4,8,14,18 are display denominators, not record counts. Offset4
        # is shared by Rating and Grand Total, so report all provisioned accounts.
        return struct.pack(
            "<7I", 0, len(entries), len(self._eligible(entries, 1)), 0, 0,
            len(self._eligible(entries, 3)), len(self._eligible(entries, 4)),
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
            raise EconomyError("rank request requires RR followed by PI")
        account = self.economy.account_view(account_key)  # Authenticated identity.
        rank_id = decimal(commands[0].payload, 9_999_999_999, error=EconomyError, what="ranking ID")
        fields = commands[1].payload.split(b"\x06")
        if len(fields) != 4 or re.fullmatch(rb"0x[0-9a-fA-F]{16}", fields[0]) is None:
            raise EconomyError("PI requires a fixed-width identity and three integers")
        identity = int(fields[0], 16)
        directory, version, selector = (
            decimal(value, maximum, error=EconomyError, what=name)
            for value, maximum, name in zip(
                fields[1:], (0xFF, 0xFFFF, 0xFFFFFFFF), ("directory", "version", "selector")
            )
        )
        if identity != account.nn:
            raise EconomyError("PI owner does not match authenticated account")
        if selector != 0:
            raise EconomyError("nonzero PI selector is not supported by this PC route")
        if rank_id not in RANK_IDS:
            return self._failure(-8811)
        snapshots = self._snapshots.setdefault(account_key, {})
        if len(snapshots) >= MAX_SNAPSHOTS or self._next_id > 0xFFFFFFFF:
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
