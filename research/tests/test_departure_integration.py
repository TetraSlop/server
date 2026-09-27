"""Opt-in departures through encrypted TCP and reentrant failed-send cleanup."""
import asyncio
from pathlib import Path
import tempfile
import unittest

from server import Server
from test_server import Client
from identities import TABLE_RAW, service_nick, player_id
from room_service import TABLE_CHANNEL
from match_service import packet, decode
from match_watchdog import MatchWatchdog


class DepartureIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration=1
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.path=Path(self.temp.name)/'state.json'
        self.server=None
        self.clients=[]
        self.live=set()
        self.received={}

    async def asyncTearDown(self):
        for client in self.clients:
            await client.close()
        if self.server is not None:await self.server.stop()
        self.temp.cleanup()

    async def start(self, *, count=2, accounts=None, rule=0, enabled=True, watchdog=None, **kwargs):
        self.server=Server(state_path=self.path,match_mode='versus',
            account_count=accounts or count,match_player_count=count,allow_departures=enabled,
            watchdog=watchdog,**kwargs)
        self.port=await self.server.start(0)
        for side,nick in enumerate(list(self.server.accounts)[:count]):
            client=Client();self.clients.append(client)
            await client.open(self.port,account=nick)
            self.assertIn(b' 422 ',await client.read())
            self.live.add(side);self.received[side]=[]
        self.keys=[c.nick.decode() for c in self.clients]
        self.peers=[self.server.nicks[c.nick] for c in self.clients]
        for side in range(count):
            await self.send(side,packet(0x41,1,body=f'@GameEN=/NN={player_id(self.clients[side].nick):016X}'))
        await self.send(0,packet(0x14,body=f'@Tet=/bm=0/du=0/st=0/cb=0/ca=0/gs={rule}/tl=0@Tab=/in=0/lu=100/ll=0/au=300/al=100/co=0/pa=0'))
        for side in range(count):
            await self.send(side,packet(1,body=f'@Req=/NN={player_id(self.clients[side].nick):016X}'))
            await self.send(side,packet(0x41,8,body='@GameOK='))
        for side in range(count):
            cards=self.server.economy.inventory(self.keys[side])[:5]
            body='@CardSelect=/C=5'+''.join(f'@N{i}=/D='+'|'.join(map(str,c.full())) for i,c in enumerate(cards))
            await self.send(side,packet(0x43,7,side,body))
        self.assertEqual(self.server.match.phase,'turn')
        self.original=self.server.match
        self.before=self.server.economy.snapshot()

    async def settle(self):
        # Wait only for explicitly tracked finite work; no timing assumptions.
        for _ in range(100):
            tasks=[t for t in (self.server.departures.task,self.server.match_task)
                   if t is not None and not t.done()]
            if not tasks:return
            await asyncio.wait_for(asyncio.gather(*(asyncio.shield(t) for t in tasks)),3)
        self.fail('departure/continuation work did not settle')

    async def flush(self,side):
        client=self.clients[side]
        result=[]
        for _ in range(2):
            await client.send(b'PING :departure-barrier')
            for _ in range(200):
                line=await client.read()
                self.assertTrue(line,'healthy connection was closed')
                if line==b'PONG departure-barrier':break
                if b' NOTICE ' in line and b' :GTM0G' in line:
                    payload=line.split(b' :',1)[1]
                    result.append(payload);self.received[side].append(payload)
            else:self.fail('response limit exceeded')
            await self.settle()
        return result

    async def flush_all(self):
        return {side:await self.flush(side) for side in sorted(self.live)}

    async def send(self,side,payload):
        await self.clients[side].send(b'NOTICE '+service_nick(TABLE_RAW)+b' :'+payload)
        return await self.flush_all()

    async def drop(self,side):
        self.live.remove(side)
        await self.clients[side].close()
        await self.flush_all()
        await self.settle()

    def notices(self,side,subtype):
        return [decode(p) for p in self.received[side]
                if decode(p).primary==0x43 and decode(p).subtype==subtype]

    async def finish(self):
        match=self.server.match
        for _ in range(50):
            if match.phase=='result':return
            self.assertIn(match.phase,('turn','battle'))
            if match.phase=='turn':
                seq=match.sequence
                for side in sorted(self.live):
                    if not match.players[side].departed and match.sequence==seq:
                        await self.send(side,packet(0x43,9,seq,f'@TurnData=/Ans={side}'))
                if match.phase!='turn' or match.sequence!=seq:continue
                side=match.active
                self.assertFalse(match.players[side].departed,'CPU move did not advance')
                await self.send(side,packet(0x43,10,seq,f'@PutData=/P={side}/H=0/F={match.legal_fields()[0]}'))
            else:
                side=match.active
                self.assertFalse(match.players[side].departed,'CPU combat did not advance')
                await self.send(side,packet(0x43,11,match.sequence,f'@BattleSelect=/B={match.targets[0]}'))
        self.fail('match did not finish')

    async def continue_leave(self,side):
        match=self.server.match
        await self.send(side,packet(0x43,14,match.sequence,f'@Ready=/Ans={side}'))
        await self.send(side,packet(0x43,17,side,'@Continue=/Ans=2/PR=0/Conf=0'))
        self.assertEqual(match.phase,'finished')
        self.assertFalse(self.server.match_reservations)
        for key in self.keys:self.assertFalse(self.server.economy.is_reserved(key))

    async def test_two_player_disconnect_substitutes_retains_then_settles_and_reconnects(self):
        await self.start(rule=0)
        await self.drop(0)
        self.assertIs(self.server.match,self.original)
        self.assertEqual(self.original.departure_seats,(0,))
        self.assertTrue(self.server.economy.is_reserved(self.keys[0]))
        self.assertEqual(len(self.notices(1,0x23)),1)
        self.assertEqual(len(self.notices(1,0x24)),1)
        retry=Client();self.clients.append(retry)
        await retry.open(self.port,account=self.peers[0].nick)
        self.assertEqual(await retry.read(),b'')
        await self.finish()
        self.assertFalse(self.server.result_rewards.has_pending(self.keys[0]))
        self.assertTrue(self.server.economy.is_reserved(self.keys[0]))
        await self.continue_leave(1)
        reconnect=Client();self.clients.append(reconnect)
        await reconnect.open(self.port,account=self.peers[0].nick)
        self.assertIn(b' 422 ',await reconnect.read())
        restored=Server(state_path=self.path)
        try:self.assertEqual(restored.economy.snapshot(),self.server.economy.snapshot())
        finally:await restored.stop()

    async def test_two_player_invalid_rule_finishes_without_growth_or_loss_result(self):
        await self.start(rule=1)
        await self.drop(1)
        self.assertEqual(self.original.phase,'finished')
        self.assertFalse(self.server.match_reservations)
        self.assertEqual(self.server.economy.snapshot(),self.before)
        self.assertEqual(len(self.notices(0,0x23)),1)
        self.assertEqual(self.notices(0,0x24),[])
        self.assertEqual(self.notices(0,0x0d),[])

    async def test_three_player_loss_rule_finishes_partial_round_and_all_profiles(self):
        await self.start(count=3,rule=2)
        await self.drop(1)
        self.assertEqual(self.original.departure_seats,(1,))
        await self.finish()
        self.assertEqual(len(self.notices(0,0x0d)),1)
        self.assertEqual(len(self.notices(2,0x0d)),1)
        for peer,key in zip(self.peers,self.keys):
            self.assertEqual(len(self.original.final_cards(peer).updated),5)
            self.assertEqual(len(self.server.economy.inventory(key)),10)
        self.assertFalse(self.server.result_rewards.has_pending(self.keys[1]))
        await self.continue_leave(0)

    async def test_explicit_quit_then_part_emits_departure_only_once(self):
        await self.start(rule=0)
        await self.clients[0].send(b'JOIN '+TABLE_CHANNEL)
        await self.flush_all()
        await self.send(0,packet(0x43,5,10,'@Quit=/No=0'))
        self.assertEqual(len(self.notices(0,5)),1)
        await self.clients[0].send(b'PART '+TABLE_CHANNEL)
        await self.flush_all()
        self.assertEqual(self.original.departure_seats,(0,))
        self.assertEqual(len(self.notices(1,0x23)),1)
        self.assertEqual(len(self.notices(1,0x24)),1)
        self.assertTrue(self.server.economy.is_reserved(self.keys[0]))
        await self.finish()
        await self.continue_leave(1)

    async def test_live_loss_quitter_receives_terminal_result_without_ready_barrier(self):
        await self.start(rule=2)
        await self.send(1,packet(0x43,5,10,'@Quit=/No=1'))
        self.assertFalse(self.peers[1].closed)
        self.assertEqual(len(self.notices(1,5)),1)
        self.assertEqual(self.original.departure_seats,(1,))
        await self.finish()
        self.assertEqual(len(self.notices(1,0x0d)),1)
        self.assertEqual(self.notices(1,0x1a),[])
        self.assertFalse(self.server.result_rewards.has_pending(self.keys[1]))
        await self.continue_leave(0)
        self.assertFalse(self.peers[1].closed)
        self.assertEqual(len(self.notices(1,0x0d)),1)

    async def test_part_without_quit_uses_same_departure_path(self):
        await self.start(count=3,rule=0)
        await self.clients[2].send(b'JOIN '+TABLE_CHANNEL)
        await self.flush_all()
        await self.clients[2].send(b'PART '+TABLE_CHANNEL)
        await self.flush_all()
        self.assertEqual(self.original.departure_seats,(2,))
        self.assertFalse(self.peers[2].closed)
        self.assertTrue(self.server.economy.is_reserved(self.keys[2]))

    async def test_failed_send_inside_locked_publish_does_not_deadlock_or_close_requester(self):
        await self.start(count=3,rule=0)
        original_send=self.peers[1].send
        async def fail(message):
            if b'@PutCard=' in message:raise OSError('injected recipient failure')
            await original_send(message)
        self.peers[1].send=fail
        for side in range(3):await self.send(side,packet(0x43,9,0,f'@TurnData=/Ans={side}'))
        self.live.remove(1)
        await asyncio.wait_for(self.send(0,packet(0x43,10,0,f'@PutData=/P=0/H=0/F={self.original.legal_fields()[0]}')),3)
        self.assertTrue(self.peers[1].closed)
        self.assertFalse(self.peers[0].closed)
        self.assertFalse(self.peers[2].closed)
        self.assertEqual(self.original.departure_seats,(1,))
        self.assertEqual(len(self.notices(0,0x23)),1)
        self.assertEqual(len(self.notices(2,0x23)),1)

    async def test_all_connections_gone_and_stop_release_every_capability(self):
        await self.start(count=3,rule=0)
        await self.drop(0)
        for side in (1,2):await self.clients[side].close()
        # Tracked accept tasks finish only after their close path detached peers.
        await asyncio.wait_for(asyncio.gather(*list(self.server.tasks)),3)
        await self.settle()
        self.assertFalse(self.server.match_reservations)
        self.assertIsNot(self.server.match,self.original)
        for key in self.keys:self.assertFalse(self.server.economy.is_reserved(key))
        await self.server.stop()

    async def test_opted_in_timeout_still_aborts_instead_of_substituting(self):
        now=[0]
        await self.start(watchdog=MatchWatchdog(clock=lambda:now[0]))
        now[0]=120
        timeout=await self.server.check_match_timeout()
        self.assertEqual(timeout.reason,'turn_ack')
        await self.settle()
        self.assertFalse(self.server.match_reservations)
        self.assertEqual(self.original.departure_seats,())
        self.assertTrue(all(p.closed for p in self.peers))

    async def test_two_player_loss_rule_ends_after_survivor_move(self):
        await self.start(rule=2)
        await self.drop(1)
        await self.finish()
        self.assertLess(self.original.sequence,10)
        self.assertEqual(len(self.notices(0,0x0d)),1)
        self.assertEqual(self.original._final_order[1],())
        self.assertEqual(len(self.original.final_cards(self.peers[1]).updated),5)
        await self.continue_leave(0)

    async def test_three_player_invalid_rule_continues_two_survivors(self):
        await self.start(count=3,rule=1)
        await self.drop(2)
        self.assertEqual(self.original.phase,'turn')
        await self.finish()
        self.assertEqual(self.original._final_order[2],())
        self.assertEqual(len(self.original.final_cards(self.peers[2]).updated),5)
        await self.continue_leave(0)

    async def test_stop_with_retained_account_aborts_without_partial_growth(self):
        await self.start(rule=0)
        await self.drop(0)
        await asyncio.wait_for(self.server.stop(),3)
        self.assertFalse(self.server.match_reservations)
        self.assertEqual(self.server.economy.snapshot(),self.before)
        for key in self.keys:self.assertFalse(self.server.economy.is_reserved(key))

    async def test_rated_departure_remains_abort_even_when_opted_in(self):
        await self.start(rating_mode='local-history')
        await self.clients[0].close()
        self.assertEqual(await self.clients[1].read(),b'')
        self.assertFalse(self.server.match_reservations)
        self.assertEqual(self.original.departure_seats,())
        self.assertEqual(self.server.economy.snapshot(),self.before)

    async def test_watchdog_waits_only_for_survivors_after_departure(self):
        now=[0]
        await self.start(watchdog=MatchWatchdog(clock=lambda:now[0]))
        await self.drop(1)
        wait=self.server.watchdog.observe(self.original,self.original)
        self.assertEqual(wait.missing_peers,(self.peers[0],))
        await self.finish()
        wait=self.server.watchdog.observe(self.original,self.original)
        self.assertEqual(wait.missing_peers,(self.peers[0],))
        await self.send(0,packet(0x43,14,self.original.sequence,'@Ready=/Ans=0'))
        wait=self.server.watchdog.observe(self.original,self.original)
        self.assertEqual(wait.reason,'result_continue')
        self.assertEqual(wait.missing_peers,(self.peers[0],))

    async def test_default_explicit_quit_remains_legacy_abort(self):
        await self.start(enabled=False)
        await self.send(0,packet(0x43,5,10,'@Quit=/No=0'))
        self.assertEqual(self.original.phase,'finished')
        self.assertEqual(self.original.departure_seats,())
        self.assertFalse(self.server.match_reservations)


if __name__=='__main__':unittest.main()
