"""A failed event driver releases its players without killing ordinary games."""
import asyncio
import unittest
from unittest.mock import patch
from identities import TABLE_RAW
from match_service import packet
from server import Server
from test_server import Client
import test_event_matches_integration as events
import test_computer_menu_integration as computer

class EventFaultIsolation(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=events.EventMatchIntegrationTests.asyncSetUp
    drain=events.EventMatchIntegrationTests.drain
    send=events.EventMatchIntegrationTests.send
    identity=events.EventMatchIntegrationTests.identity
    enter_selection=events.EventMatchIntegrationTests.enter_selection
    admit=events.EventMatchIntegrationTests.admit
    engine=events.EventMatchIntegrationTests.engine
    request=computer.ComputerMenuIntegrationTests.request
    choose=computer.ComputerMenuIntegrationTests.choose
    play_cpu=computer.ComputerMenuIntegrationTests.play

    async def asyncTearDown(self):
        if hasattr(self,'client'):await self.client.close()
        await events.EventMatchIntegrationTests.asyncTearDown(self)

    async def start(self):
        self.server=Server(state_path=self.path,account_count=3,event_configs=(self.cfg,),
            event_tables=(self.table,),event_clock=lambda:200)
        port=await self.server.start(0);keys=list(self.server.accounts)
        self.clients=[Client(),Client()];self.inbox=[[],[]]
        for client,nick in zip(self.clients,keys):
            await client.open(port,account=nick);self.assertIn(b' 422 ',await client.read())
        self.keys=[c.nick.decode()for c in self.clients]
        self.client=Client();await self.client.open(port,account=keys[2])
        self.assertIn(b' 422 ',await self.client.read());self.key=self.client.nick.decode()

    async def menu(self):
        view=self.server.economy.account_view(self.key)
        identity=f'/NN={view.nn:016X}/HID={view.player_id}'
        await self.request(TABLE_RAW,packet(0x41,0x12,body='@GameENC='+identity))
        await self.request(TABLE_RAW,packet(1,body='@Req='+identity))
        self.assertEqual(self.server.match.phase,'computer_menu')

    async def failed(self,global_storage=False):
        await self.start();await self.admit();await self.menu();await self.choose(1)
        before=self.server.match.snapshot()
        def fail():
            if global_storage:self.server.storage_failed=OSError('injected shared storage failure')
            raise ValueError('injected event driver failure')
        adapter=self.server.event_matches
        with patch.object(adapter.coordinator,'poll',side_effect=fail):
            await asyncio.wait_for(asyncio.shield(adapter.task),3)
        self.assertIsNotNone(adapter.failure)
        for client in self.clients:
            self.assertNotIn(client.nick,self.server.nicks)
            self.assertFalse(self.server.economy.is_reserved(client.nick.decode()))
        if global_storage:
            self.assertNotIn(self.client.nick,self.server.nicks)
            self.assertFalse(self.server.economy.is_reserved(self.key))
        else:
            self.assertIn(self.client.nick,self.server.nicks)
            self.assertEqual(self.server.match.snapshot(),before)
            await self.play_cpu()
            await self.request(TABLE_RAW,packet(0x43,17,0,'@Continue=/Ans=2/PR=0/Conf=0'))
            self.assertEqual(self.server.match.phase,'finished')

    async def test_event_failure_preserves_and_finishes_ordinary_match(self):await self.failed()
    async def test_shared_storage_failure_still_closes_every_affected_session(self):await self.failed(True)

if __name__=='__main__':unittest.main()
