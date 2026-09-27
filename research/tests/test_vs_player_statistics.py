"""Original counter behavior, wire consumption and authoritative persistence."""
from dataclasses import replace
import json
import unittest

from economy_service import EconomyError,EconomyService
from rating_policy import RatingPolicy
from result_stats import PracticeStats,ResultStatsError
from test_result_stats import VM,SEEDED,economy
import test_rating_policy as rating_tests


class VsPlayerStatisticsTests(unittest.TestCase):
    def test_original_offline_mode_and_online_final_count(self):
        for initial in (0,7,65535):
            for mode in (0,1,2):
                final=replace(SEEDED,event_count=initial).next_result(1,vs_player_game=mode==2)
                for online in (False,True):
                    with self.subTest(initial=initial,mode=mode,online=online):
                        v=VM();p,g=v.player,v.game
                        v.w8(p+0xea,online);v.w8(g+0x18a,mode);v.w8(g+0x196,10)
                        v.w32(p+0xdc,0);v.w16(p+0x84,SEEDED.count)
                        v.w16(p+0x86,SEEDED.sequence);v.w16(p+0xb8,initial)
                        v.w32(p+0xb0,SEEDED.rt_points);v.w32(p+0xb4,SEEDED.prize_points)
                        for i,score in enumerate((7,3)):
                            v.w8(p+0x1a0+i*0x30,score);v.w32(p+0x180+i*0x30,300)
                            v.w8(p+0x19b+i*0x30,i)
                        if online:
                            body='43000D00@ResultData=/R=0|1|2|3|4|5|6|7/P=2'
                            body+=final.result_section(0)+PracticeStats().next_result(-1).result_section(1)
                            v.queue(body);self.assertEqual(v.receive(0x0d),1)
                        v.call(0x100cbd80,this=g)
                        self.assertEqual(v.r16(p+0xb8),final.vs_player_games)
                        self.assertEqual(v.r32(p+0xb0),SEEDED.rt_points)
                        self.assertEqual(v.r32(p+0xb4),SEEDED.prize_points)

    def test_mode_type_and_loss_draw_win_each_count(self):
        for outcome in (-1,0,1):
            self.assertEqual(SEEDED.next_result(outcome,vs_player_game=True).vs_player_games,9)
            self.assertEqual(SEEDED.next_result(outcome).vs_player_games,8)
        for invalid in (None,0,1,'versus'):
            with self.assertRaises(ResultStatsError):SEEDED.next_result(1,vs_player_game=invalid)

    def test_two_three_human_count_commits_with_both_rating_modes_and_restart(self):
        for n,scores in ((2,(7,3)),(3,(6,6,3))):
            for mode in ('preserve','local-history'):
                with self.subTest(n=n,mode=mode):
                    service=rating_tests.economy(n);policy=RatingPolicy(service,mode=mode)
                    rounds,stats,changes=rating_tests.prepare(service,policy,scores)
                    stats={k:replace(v,event_count=10)for k,v in stats.items()}
                    policy.commit_growth(rounds,stats,rating_changes_by_key=changes,vs_player_game=True)
                    saved=service.snapshot();journal=policy.snapshot()
                    with self.assertRaises(EconomyError):
                        policy.commit_growth(rounds,stats,rating_changes_by_key=changes,vs_player_game=True)
                    self.assertEqual(service.snapshot(),saved);self.assertEqual(policy.snapshot(),journal)
                    restored=EconomyService.restore(json.loads(json.dumps(saved)))
                    for key,expected in stats.items():
                        self.assertEqual(PracticeStats.from_profile(restored.profile_bytes(key)),expected)

    def test_last_invalid_counter_rejects_all_profiles_and_rating_journal(self):
        for mode in ('preserve','local-history'):
            service=rating_tests.economy();policy=RatingPolicy(service,mode=mode)
            rounds,stats,changes=rating_tests.prepare(service,policy)
            stats={k:replace(v,event_count=10)for k,v in stats.items()}
            stats['third']=replace(stats['third'],event_count=11)
            saved=service.snapshot();journal=policy.snapshot()
            with self.assertRaises(EconomyError):
                policy.commit_growth(rounds,stats,rating_changes_by_key=changes,vs_player_game=True)
            self.assertEqual(service.snapshot(),saved);self.assertEqual(policy.snapshot(),journal)

    def test_counter_authority_rejects_single_human_missing_stats_and_unspecified_mode(self):
        service=economy();keys=tuple(service.snapshot()['accounts'])
        rounds=[];stats={}
        for key in keys[:2]:
            token=service.reserve_match(key);cards=service.inventory(key)[:5]
            rounds.append((token,cards,cards))
            stats[key]=PracticeStats.from_profile(service.profile_bytes(key)).next_result(1,vs_player_game=True)
        before=service.snapshot()
        for inputs,values,mode in ((rounds[:1],{keys[0]:stats[keys[0]]},True),
                                   (rounds,None,True),(rounds,stats,False),
                                   (rounds,stats,1),(rounds,stats,None)):
            with self.assertRaises(EconomyError):service.commit_match_growth(inputs,values,vs_player_game=mode)
            self.assertEqual(service.snapshot(),before)


if __name__=='__main__':unittest.main()
