"""Original owner observation choices govern ordinary table watch admission."""
import unittest
from pathlib import Path
import sys
import test_spectator_integration as base
from match_service import decode,packet
from identities import player_id
from room_service import TABLE_CHANNEL


class OriginalObserverRules(unittest.TestCase):
 def test_original_observe_gate_and_watch_chat_panel_use_production_rule(self):
  sys.path.insert(0,str(Path(__file__).parents[1]/'protocol_map'))
  from observer_rules_vectors import cases
  self.assertEqual(len(cases()),9)


class ObserveDisabledIntegration(unittest.IsolatedAsyncioTestCase):
 observation_rule=1
 asyncSetUp=base.SpectatorIntegrationTests.asyncSetUp
 asyncTearDown=base.SpectatorIntegrationTests.asyncTearDown
 player=base.SpectatorIntegrationTests.player
 read_watch=base.SpectatorIntegrationTests.read_watch

 async def test_advertised_impossible_blocks_discovery_and_admission_without_disconnect(self):
  c=self.clients[1];match=self.server.match
  before=match.snapshot();tokens=dict(self.server.match_reservations)
  await c.send(b'NOTICE '+self.target+b' :'+packet(45,body='@CheckJoinTable='))
  self.assertEqual(decode(await self.read_watch()).sections,{'CheckJoinTable_Ans':{'Join':'0','Mem':'0'}})
  await c.join(TABLE_CHANNEL)
  await c.send(b'NOTICE '+self.target+b' :'+packet(1,body=f'@Req=/NN={player_id(c.nick):016X}/HID=2'))
  self.assertEqual(decode(await self.read_watch()).sections,{'PLAYACK':{'EN':'-32869'}})
  await c.send(b'PING :still-in-lobby');self.assertEqual(await c.read(),b'PONG still-in-lobby')
  peer=self.server.nicks[c.nick]
  self.assertNotIn(peer,self.server.spectator_admissions)
  self.assertEqual(self.server.spectators.count,0)
  self.assertEqual(match.snapshot(),before);self.assertEqual(self.server.match_reservations,tokens)
  self.assertFalse(self.server.economy.is_reserved(c.nick.decode()))


class ObserveNoCommentsAdmission(unittest.IsolatedAsyncioTestCase):
 observation_rule=2
 asyncSetUp=base.SpectatorIntegrationTests.asyncSetUp
 asyncTearDown=base.SpectatorIntegrationTests.asyncTearDown
 player=base.SpectatorIntegrationTests.player
 read_watch=base.SpectatorIntegrationTests.read_watch
 admit_watch=base.SpectatorIntegrationTests.admit_watch
 request_watch=base.SpectatorIntegrationTests.request_watch

 async def test_no_comments_choice_still_allows_public_gameplay_observation(self):
  await self.admit_watch();await self.request_watch()
  packets=[decode(await self.read_watch())for _ in range(3)]
  self.assertEqual([p.subtype for p in packets],[0x25,0x26,9])
  self.assertEqual(packets[0].sections['WatchInfo']['R'].split('|')[-1],'2')
  self.assertEqual(self.server.spectators.count,1)
  self.assertEqual(self.server.match.table_rules['in'],2)

if __name__=='__main__':unittest.main()
