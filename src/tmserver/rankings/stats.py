"""Result and profile statistics, with optional money credits.

Client readers: 10104282..1010448F, profile importer 101011F0, result setup
100CC63A..100CC906. Server choice: practice counts and streaks advance; practice
keeps reward totals unless a reward is passed, which updates the money statistics
without choosing a payout formula. Rating changes only when a ring quote is
supplied. Human matches advance VS. Player Games. RN (Memorable Win), RT (VS.
Rating, +128) and prize points (+134) are never written. Details: TetraPackets
client/ratings-and-statistics.md.
"""

import struct
from dataclasses import dataclass, replace

from tmserver.economy.errors import EconomyError

PROFILE_SIZE = 0x3028
RN = slice(0x7D, 0x9D)
# TM0DataFile statistics: field, profile offset, little-endian struct format.
LAYOUT = (
    ("rating", 0x30, "I"),
    ("count", 0x40, "H"),
    ("sequence", 0x44, "H"),
    ("max_reward", 0x48, "I"),
    ("average_reward", 0x4C, "I"),
    ("reward_sum", 0x50, "I"),
    ("streak", 0x78, "h"),
    ("best_streak", 0x7A, "h"),
    ("vs_player_games", 0x130, "H"),
)
_RANGES = {
    "I": (0, 0xFFFFFFFF, "an unsigned DWORD"),
    "H": (0, 65535, "an unsigned WORD"),
    "h": (-32768, 32767, "a signed WORD"),
}


def result_outcome(result_class):
    """-1 loss, 0 draw, +1 win for a native result class (lower is better)."""
    return (result_class < 2) - (result_class > 2)


def _profile(profile):
    if len(profile) != PROFILE_SIZE:
        raise EconomyError("expected canonical TM0DataFile size")
    return profile


def _signed_word(value):
    return ((value + 32768) & 65535) - 32768


@dataclass(frozen=True)
class PracticeStats:
    rating: int = 300
    count: int = 0
    sequence: int = 0
    max_reward: int = 0
    average_reward: int = 0
    reward_sum: int = 0
    streak: int = 0
    best_streak: int = 0
    rn: bytes = b""
    vs_player_games: int = 0
    current_reward: int = 0  # ResultData D1 only, not a field in TM0DataFile.

    def __post_init__(self):
        for name, _, kind in LAYOUT:
            value, (low, high, label) = getattr(self, name), _RANGES[kind]
            if type(value) is not int or not low <= value <= high:
                raise EconomyError(f"{name} must be {label}")
        if not isinstance(self.rn, bytes) or len(self.rn) > 31 or b"\0" in self.rn:
            raise EconomyError("RN must leave room for a terminator in its 32-byte buffer")
        if type(self.current_reward) is not int or not 0 <= self.current_reward <= 99_999_999:
            raise EconomyError("current reward must be an actual bounded money credit")

    @classmethod
    def from_profile(cls, profile):
        _profile(profile)
        return cls(
            **{name: struct.unpack_from("<" + kind, profile, at)[0] for name, at, kind in LAYOUT},
            rn=bytes(profile[RN]).split(b"\0", 1)[0][:31],
        )

    def next_result(
        self, outcome, reward=0, *, fully_tied=None, rating_change=None, vs_player_game=False
    ):
        """outcome=-1 loss, 0 draw, +1 win; returns a new value.

        Streaks, the WORD sequence and the DWORD reward sum wrap as the original
        offline bookkeeping does. D5 saturates at 65535: the online client
        divides by it. Zero reward and fully tied refunds leave reward sum and
        maximum alone (100CC7B3..100CC7CB, 100CC8D3..100CC8EC); a three-player
        middle finish can be a draw without being fully tied. rating_change (a
        RatingQuote, checked when RatingPolicy commits it) sets D2.
        vs_player_game advances T2/profile+130 (100CC84F);
        online clients read that count without incrementing it again.
        """
        if type(outcome) is not int or outcome not in (-1, 0, 1):
            raise EconomyError("outcome must be -1,0,1")
        if type(reward) is not int or not 0 <= reward <= 99_999_999:
            raise EconomyError("reward must be an actual bounded money credit")
        if type(vs_player_game) is not bool:
            raise EconomyError("VS. Player Games requires a boolean match mode")
        if fully_tied is None:
            fully_tied = outcome == 0
        if type(fully_tied) is not bool or fully_tied and outcome != 0:
            raise EconomyError("fully tied result must have draw outcome")
        rating = self.rating if rating_change is None else rating_change.d2
        if outcome > 0:
            streak = 1 if self.streak < 0 else _signed_word(self.streak + 1)
        elif outcome < 0:
            streak = -1 if self.streak > 0 else _signed_word(self.streak - 1)
        else:
            streak = 0
        best = max(self.best_streak, streak) if outcome > 0 else self.best_streak
        count = min(self.count + 1, 65535)
        reward_sum = (self.reward_sum + (0 if fully_tied else reward)) & 0xFFFFFFFF
        maximum = self.max_reward if fully_tied else max(self.max_reward, reward)
        return replace(
            self,
            rating=rating,
            count=count,
            sequence=(self.sequence + 1) & 65535,
            vs_player_games=(self.vs_player_games + int(vs_player_game)) & 65535,
            streak=streak,
            best_streak=best,
            average_reward=reward_sum // count,
            max_reward=maximum,
            reward_sum=reward_sum,
            current_reward=reward,
        )

    def result_section(self, seat):
        """Bounded ordinary ResultData section; D0 is neutral/uninterpreted."""
        if type(seat) is not int or seat not in (0, 1, 2):
            raise EconomyError("ordinary seat must be 0..2")
        if self.count == 0:
            raise EconomyError("ResultData D5 must not be zero")
        values = (
            0,
            self.current_reward,
            self.rating,
            self.streak,
            self.sequence,
            self.count,
            self.reward_sum,
        )
        return (
            f"@No{seat}=/D="
            + "|".join(map(str, values))
            + f"/T=0|0|{self.vs_player_games}/RN="
            + self.rn.hex().upper()
        )

    def apply_to_profile(self, profile):
        """Update mapped fields only; preserve unrelated bytes, cards and money."""
        output = bytearray(_profile(profile))
        for name, offset, kind in LAYOUT:
            struct.pack_into("<" + kind, output, offset, getattr(self, name))
        output[RN] = self.rn.ljust(32, b"\0")
        return bytes(output)
