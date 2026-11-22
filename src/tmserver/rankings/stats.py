"""Result and profile statistics, with optional money credits.

Client readers: 10104282..1010448F, profile importer 101011F0, result setup
100CC63A..100CC906. Server choice: practice counts and streaks advance; practice
keeps reward totals unless a reward is passed, which updates the money statistics
without choosing a payout formula. Rating changes only when a ring quote is
supplied. Human matches advance VS. Player Games. RN (Memorable Win), RT (VS.
Rating) and prize points do not change. Details: TetraPackets
client/ratings-and-statistics.md.
"""

import struct
from dataclasses import dataclass, replace

from tmserver.rankings.errors import RankingError

ResultStatsError = RankingError  # Name still imported by economy/service.py.


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
    rt_points: int = 0
    event_count: int = 0  # Legacy API name: original label is VS. Player Games.
    prize_points: int = 0
    current_reward: int = 0  # ResultData D1 only, not a field in TM0DataFile.

    def __post_init__(self):
        for name in (
            "rating",
            "max_reward",
            "average_reward",
            "reward_sum",
            "rt_points",
            "prize_points",
        ):
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value <= 0xFFFFFFFF:
                raise RankingError(name + " must be an unsigned DWORD")
        for name in ("count", "sequence", "event_count"):
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value <= 65535:
                raise RankingError(name + " must be an unsigned WORD")
        for name in ("streak", "best_streak"):
            value = getattr(self, name)
            if type(value) is not int or not -32768 <= value <= 32767:
                raise RankingError(name + " must be a signed WORD")
        if not isinstance(self.rn, bytes) or len(self.rn) > 31 or b"\0" in self.rn:
            raise RankingError("RN must leave room for a terminator in its 32-byte buffer")
        if type(self.current_reward) is not int or not 0 <= self.current_reward <= 99_999_999:
            raise RankingError("current reward must be an actual bounded money credit")

    @classmethod
    def from_profile(cls, profile):
        if len(profile) != 0x3028:
            raise RankingError("expected canonical TM0DataFile size")
        u16 = lambda offset: struct.unpack_from("<H", profile, offset)[0]
        u32 = lambda offset: struct.unpack_from("<I", profile, offset)[0]
        return cls(
            rating=u32(0x30),
            count=u16(0x40),
            sequence=u16(0x44),
            max_reward=u32(0x48),
            average_reward=u32(0x4C),
            reward_sum=u32(0x50),
            streak=struct.unpack_from("<h", profile, 0x78)[0],
            best_streak=struct.unpack_from("<h", profile, 0x7A)[0],
            rn=bytes(profile[0x7D:0x9D]).split(b"\0", 1)[0][:31],
            rt_points=u32(0x128),
            event_count=u16(0x130),
            prize_points=u32(0x134),
        )

    @property
    def vs_player_games(self):
        return self.event_count

    def next_result(
        self, outcome, reward=0, *, fully_tied=None, rating_change=None, vs_player_game=False
    ):
        """outcome=-1 loss,0 draw,+1 win; inputs/state remain immutable.

        Signed streak transitions and WORD sequence wrap match the original
        offline bookkeeping. D5 saturates at 65535 instead of wrapping to 0:
        the online client divides by D5 without a zero check. Zero reward leaves
        the previous total/maximum intact; average must match total/new count.
        Fully tied refunds do not increase reward sum or maximum, matching
        100CC7B3..100CC7CB and 100CC8D3..100CC8EC. A three-player middle finish
        may have outcome0 without being fully tied; callers must distinguish it.
        Native cumulative sum wrapsDWORD. current_reward is transient D1 only.
        Explicit rating_change is a validated original-ring RatingQuote from a
        trusted policy. It changes only D2; default transitions retain rating.
        vs_player_game is the trusted match mode, not a client field. It advances
        T2/profile+130 with the original offline mode2 WORD wrap (100CC84F).
        Online clients consume this final count without incrementing it again.
        """
        if type(outcome) is not int or outcome not in (-1, 0, 1):
            raise RankingError("outcome must be -1,0,1")
        if type(reward) is not int or not 0 <= reward <= 99_999_999:
            raise RankingError("reward must be an actual bounded money credit")
        if type(vs_player_game) is not bool:
            raise RankingError("VS. Player Games requires a boolean match mode")
        if fully_tied is None:
            fully_tied = outcome == 0
        if type(fully_tied) is not bool or fully_tied and outcome != 0:
            raise RankingError("fully tied result must have draw outcome")
        rating = self.rating
        if rating_change is not None:
            from tmserver.rankings.history import validate_quote

            change = validate_quote(rating_change)
            if (
                change.before.sequence != self.sequence
                or change.before.revision > 0
                and change.before.rating != self.rating
            ):
                raise RankingError("rating history differs from the current profile")
            if outcome != (change.result_class < 2) - (change.result_class > 2) or fully_tied != (
                len(set(change.scores)) == 1
            ):
                raise RankingError("rating quote and result outcome disagree")
            rating = change.d2
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
            event_count=(self.event_count + int(vs_player_game)) & 65535,
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
            raise RankingError("ordinary seat must be 0..2")
        if self.count == 0:
            raise RankingError("ResultData D5 must not be zero")
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
            + f"/T=0|0|{self.event_count}/RN="
            + self.rn.hex().upper()
        )

    def apply_to_profile(self, profile):
        """Update mapped fields only; preserve unrelated bytes, cards and money."""
        if len(profile) != 0x3028:
            raise RankingError("expected canonical TM0DataFile size")
        output = bytearray(profile)
        for offset, value in (
            (0x30, self.rating),
            (0x48, self.max_reward),
            (0x4C, self.average_reward),
            (0x50, self.reward_sum),
            (0x128, self.rt_points),
            (0x134, self.prize_points),
        ):
            struct.pack_into("<I", output, offset, value)
        for offset, value in ((0x40, self.count), (0x44, self.sequence), (0x130, self.event_count)):
            struct.pack_into("<H", output, offset, value)
        struct.pack_into("<hh", output, 0x78, self.streak, self.best_streak)
        output[0x7D:0x9D] = self.rn.ljust(32, b"\0")
        return bytes(output)
