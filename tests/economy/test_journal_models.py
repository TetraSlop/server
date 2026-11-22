"""The match, Prize Center and result reward journals keep strict saved shapes."""

import copy
import json
import unittest

from tests.support.golden_save import SNAPSHOT
from tmserver.economy.auctions.ledger import AuctionEconomy
from tmserver.economy.errors import EconomyError
from tmserver.economy.match_ledger import MatchEconomy
from tmserver.economy.prize_center import PrizeService
from tmserver.economy.result_rewards import ResultRewardService
from tmserver.economy.service import EconomyService

OWNER, HOLDER = "UGITW5T4U", "UGITW5T46"

REJECTED = (
    ("match_economy", lambda d: d["rounds"][0].update(stake=True)),
    ("match_economy", lambda d: d["rounds"][0].update(closed=1)),
    ("match_economy", lambda d: d["rounds"][0].update(handed=[True, 1])),
    ("match_economy", lambda d: d["rounds"][0]["payouts"].update({"0": 200.0})),
    ("match_economy", lambda d: d["rounds"][0].update(extra=1)),
    ("match_economy", lambda d: d["rounds"][0].pop("closed")),
    ("match_economy", lambda d: d["rounds"][0].update(transfer_digest="A" * 64)),
    ("match_economy", lambda d: d.update(version=True)),
    ("prize", lambda d: d["accounts"][OWNER].update(last_session=True)),
    ("prize", lambda d: d["accounts"][HOLDER]["pending"].update(lucky=True)),
    ("prize", lambda d: d["accounts"][HOLDER]["pending"].update(lucky_cards=[1, 2])),
    ("prize", lambda d: d["accounts"][HOLDER].update(known_awards=["award.pending"] * 2)),
    ("result_rewards", lambda d: d["pending"][0].update(seat=True)),
    ("result_rewards", lambda d: d["known"][0].update(digest="x")),
    ("result_rewards", lambda d: d.update(extra=[])),
)


class JournalModelTests(unittest.TestCase):
    def setUp(self):
        self.golden = json.loads(SNAPSHOT.read_text(encoding="utf8"))
        economy = EconomyService.restore(self.golden)
        auction = AuctionEconomy.restore(economy, self.golden["auction"])
        self.restore = {
            "match_economy": lambda d: MatchEconomy.restore(economy, d),
            "prize": lambda d: PrizeService.restore(economy, d),
            "result_rewards": lambda d: ResultRewardService.restore(economy, auction, d),
        }

    def test_golden_journals_round_trip(self):
        for name, restore in self.restore.items():
            with self.subTest(journal=name):
                self.assertEqual(restore(self.golden[name]).snapshot(), self.golden[name])

    def test_bools_floats_and_unknown_shapes_are_rejected(self):
        for name, mutate in REJECTED:
            bad = copy.deepcopy(self.golden[name])
            mutate(bad)
            with self.subTest(journal=name, document=bad), self.assertRaises(EconomyError):
                self.restore[name](bad)


if __name__ == "__main__":
    unittest.main()
