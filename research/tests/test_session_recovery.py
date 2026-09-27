"""Headless regression checks for session ownership and delivery failures.

No listener, native client, persistent save or proprietary library is opened.
The writer substitutes only transport I/O; real services own all state changes.
"""
import asyncio
import unittest
from unittest.mock import patch

from identities import TABLE_RAW, ROOM_RAW, service_nick,game_id
from event_matchmaking import EventDelivery
from economy_service import named
from match_service import packet
from server import ACCOUNTS, Server, Session


class Writer:
    def __init__(self, fail=False):
        self.closed = False
        self.fail = fail
        self.writes = []

    def get_extra_info(self, name):
        return ('127.0.0.1', 51241)

    def write(self, data):
        self.writes.append((self.closed, data))

    async def drain(self):
        if self.fail:
            raise OSError('simulated recipient failure')

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


class SessionRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server = Server()

    async def asyncTearDown(self):
        await self.server.stop()

    def peer(self, index=0, fail=False):
        peer = Session(self.server, None, Writer(fail), index + 1)
        peer.nick = list(ACCOUNTS)[index]
        self.server.ensure_account(peer.nick)
        self.server.trade.bind(peer, peer.nick.decode('ascii'))
        self.server.nicks[peer.nick] = peer
        self.server.sessions.add(peer)
        return peer

    async def admit(self, peer):
        await self.server.dispatch_table(peer, packet(0x41, 1, body='@GameEN='))

    async def test_failed_room_recipient_preserves_requester_and_other_delivery(self):
        healthy = self.peer()
        failed = self.peer(1, fail=True)
        notice = b'GTM0L<DD>\x070\x07'
        # A table admission publishes room metadata before its GameEA answer.
        # The failed observer must not turn that into the requester's exception.
        with patch.object(self.server.room, 'set_table', return_value=[(failed, notice), (healthy, notice)]):
            await healthy.command(b'NOTICE', [service_nick(TABLE_RAW), packet(0x41, 1, body='@GameEN=')])
        self.assertFalse(healthy.closed)
        self.assertIs(self.server.nicks[healthy.nick], healthy)
        self.assertTrue(any(p.peer is healthy for p in self.server.match.players))
        self.assertTrue(any(notice in data for _, data in healthy.writer.writes))
        self.assertTrue(any(b'@GameEA=/EN=1' in data for _, data in healthy.writer.writes))

    async def test_close_detaches_before_waiting_for_notifications(self):
        peer = self.peer()
        await self.admit(peer)
        original = self.server.match
        peer.channels.add(b'#test')
        self.server.channels[b'#test'] = {peer}
        entered, release = asyncio.Event(), asyncio.Event()

        async def blocked(deliveries, *, source):
            self.assertEqual(source,service_nick(ROOM_RAW))
            entered.set()
            await release.wait()

        with patch.object(self.server, 'room_updates', side_effect=blocked):
            closing = asyncio.create_task(peer.close())
            try:
                await asyncio.wait_for(entered.wait(), 1)
                self.assertTrue(peer.closed)
                self.assertTrue(peer.writer.closed)
                self.assertNotIn(peer.nick, self.server.nicks)
                self.assertNotIn(peer, self.server.sessions)
                self.assertNotIn(b'#test', self.server.channels)
                self.assertIsNot(original, self.server.match)
                self.assertNotIn(peer, self.server.match_reservations)
                # Rebinding the same account while old notifications wait is safe.
                replacement = self.peer()
            finally:
                release.set()
                await closing
        self.assertIs(self.server.nicks[replacement.nick], replacement)

    async def test_old_event_notifications_cannot_clear_reconnected_shop_session(self):
        old=self.peer();observer=self.peer(1);key=old.nick.decode()
        entered,release=asyncio.Event(),asyncio.Event()
        original=observer.send;notice=b'event-close-notification'
        async def slow(message):
            if notice in message:entered.set();await release.wait()
            await original(message)
        def cleanup(peer):
            return [EventDelivery(observer,game_id(ROOM_RAW),notice)]if peer is old else []
        with patch.object(observer,'send',side_effect=slow),patch.object(self.server.event_matches.coordinator,'disconnect',side_effect=cleanup):
            closing=asyncio.create_task(old.close())
            try:
                await asyncio.wait_for(entered.wait(),1)
                replacement=self.peer();view=self.server.economy.account_view(key)
                request=named(0xb0,0,0,f'@ShReq=/NN={view.nn:016X}/HID=0/Dm=0/Vol=0/CN={view.name.hex()}')
                opened=self.server.economy.handle(key,request)
                self.assertTrue(self.server.economy.is_busy(key))
            finally:
                release.set();await closing
                if self.server.event_matches.sender is not None:await self.server.event_matches.sender
        self.assertIs(self.server.nicks[replacement.nick],replacement)
        self.assertTrue(self.server.economy.is_busy(key))
        self.assertEqual(self.server.economy.handle(key,request),opened)

    async def test_non_admission_packets_do_not_claim_or_reserve_table(self):
        peer = self.peer()
        requests = [packet(0x12, body='@Rule='),
                    packet(0x2d, body='@CheckJoinTable='),
                    packet(0x43, 9, body='@TurnData=/Ans=0'),
                    packet(0x43, 0x27, 3, '@Break=/ID=1'),
                    packet(0x41, 5, body='@GameQU='), b'unrelated']
        for request in requests:
            with self.subTest(request=request):
                try:
                    await self.server.dispatch_table(peer, request)
                except ValueError:
                    pass  # Explicit rejection is also compatible with this guard.
                self.assertEqual(self.server.match.players, [])
                self.assertNotIn(peer, self.server.match_reservations)
                self.assertFalse(self.server.economy.is_reserved(peer.nick.decode('ascii')))

    async def test_cleanup_stage_failure_still_releases_table(self):
        peer = self.peer()
        await self.admit(peer)
        original = self.server.match
        with patch.object(self.server.trade, 'disconnect', side_effect=ValueError('cleanup-stage fault')):
            await peer.close()
        self.assertIsNot(original, self.server.match)
        self.assertNotIn(peer, self.server.match_reservations)
        self.assertFalse(self.server.economy.is_reserved(peer.nick.decode('ascii')))
        self.assertTrue(peer.writer.closed)
        self.assertNotIn(peer.nick, self.server.nicks)

    async def test_send_waiting_on_lock_rechecks_closed_state(self):
        peer = self.peer()
        await peer.send_lock.acquire()
        sending = asyncio.create_task(peer.send(b'PING :queued'))
        await asyncio.sleep(0)
        try:
            await peer.close()
        finally:
            peer.send_lock.release()
        await sending
        self.assertEqual(peer.writer.writes, [])

    async def test_failed_receiver_cleanup_cannot_overtake_current_room_batch(self):
        healthy=self.peer()
        failed=self.peer(1,fail=True)
        first=b'old-room-sequence'
        newer=b'new-room-sequence'
        with patch.object(self.server.room,'leave',side_effect=lambda p:[(healthy,newer)] if p is failed else []):
            await self.server.room_updates([(failed,first),(healthy,first)])
            task=self.server.room_task
            if task is not None:await task
        lines=[data for _,data in healthy.writer.writes]
        self.assertEqual(len(lines),2)
        self.assertIn(first,lines[0])
        self.assertIn(newer,lines[1])

    async def test_rejected_admissions_leave_no_provisional_reservations(self):
        owner=self.peer()
        other=self.peer(1)
        await self.admit(owner)
        original=self.server.match
        await self.admit(other)
        self.assertFalse(other.closed)
        self.assertTrue(any(b'@GameEA=/EN=-32870' in data for _,data in other.writer.writes))
        self.assertIs(original,self.server.match)
        self.assertEqual([p.peer for p in original.players],[owner])
        self.assertNotIn(other,self.server.match_reservations)
        self.assertFalse(self.server.economy.is_reserved(other.nick.decode()))


if __name__ == '__main__':
    unittest.main()
