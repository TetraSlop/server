"""Native Computer-menu observers use real NPC identities and safe names."""
import asyncio,sys,tempfile,unittest
from pathlib import Path
from dataclasses import replace
from server import Server
from test_server import Client
from identities import TABLE_RAW,service_nick,player_id
from match_service import MatchService,packet,decode
from spectator_service import SpectatorService,bootstrap,SpectatorError
import test_spectator_integration as base
import test_observer_rules as rules

class OriginalNPCObserver(unittest.TestCase):
 def test_all_original_catalog_names_and_viewer_level_thresholds(self):
  sys.path.insert(0,str(Path(__file__).parents[1]/'protocol_map'))
  from npc_observer_vectors import cases
  self.assertEqual(len(cases()),89)

 def test_long_npc_roster_label_is_terminated_in_original_current_roster(self):
  sys.path.insert(0,str(Path(__file__).parents[1]/'protocol_map'))
  from table_admission_vectors import AdmissionVM
  m=MatchService(computer_menu=True);m.register('human',1,b'Alice')
  m.handle('human',packet(65,18,body='@GameENC='));m.handle('human',packet(1,body='@Req='))
  m.handle('human',packet(67,6,2,'@ComGame=/Rule=0|0|0|0|2|0|0|1/Com=1'))
  v=AdmissionVM();v.call(0x100a8ff0,this=v.controller)
  v.queue(packet(65,4,body=m.member_list('human'))[5:].decode())
  v.call(0x100a9180,this=v.controller)
  self.assertEqual(v.r32(v.controller+0x108),2)
  self.assertEqual(v.text(v.controller+0x190,16),'Flower Girl Nat')
  self.assertEqual(v.u.mem_read(v.controller+0x19f,1),b'\0')
  self.assertEqual(m.players[1].name,b'Flower Girl Natasha')
  snapshot=m.observer_snapshot()
  for changed in (replace(snapshot.players[0],npc_id=1),replace(snapshot.players[1],npc_id=31),
                  replace(snapshot.players[1],departed=True),replace(snapshot.players[1],substitute=1)):
   with self.assertRaises(SpectatorError):bootstrap(replace(snapshot,players=(snapshot.players[0],changed)))

class NPCObserverIntegration(unittest.IsolatedAsyncioTestCase):
 player=base.SpectatorIntegrationTests.player
 read_watch=base.SpectatorIntegrationTests.read_watch
 admit_watch=base.SpectatorIntegrationTests.admit_watch
 request_watch=base.SpectatorIntegrationTests.request_watch
 asyncTearDown=base.SpectatorIntegrationTests.asyncTearDown

 async def asyncSetUp(self):
  asyncio.get_running_loop().slow_callback_duration=1
  self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
  self.server=Server(state_path=Path(self.temp.name)/'state.json',spectator=SpectatorService(
   enabled=True,bootstrap_delay=0,turn_delay=0,action_delay=0,packet_delay=0))
  port=await self.server.start(0);self.clients=[];self.target=service_nick(TABLE_RAW)
  for nick in self.server.accounts:
   client=Client();self.clients.append(client);await client.open(port,account=nick)
   self.assertIn(b' 422 ',await client.read())
  identity=f'{player_id(self.clients[0].nick):016X}'
  await self.player(packet(65,18,body=f'@GameENC=/NN={identity}/HID=1'))
  await self.player(packet(1,body=f'@Req=/NN={identity}/HID=1'))
  observation=getattr(self,'observation_rule',0)
  await self.player(packet(67,6,2,f'@ComGame=/Rule=0|0|0|0|2|0|{observation}|1/Com=1'))
  cards=self.server.economy.inventory(self.clients[0].nick.decode())[:5]
  await self.player(packet(67,7,0,'@CardSelect=/C=5'+''.join(f'@N{i}=/D='+'|'.join(map(str,c.full()))for i,c in enumerate(cards))))

 test_complete_original_NPC_round=base.SpectatorIntegrationTests.test_real_watch_bootstrap_and_full_match_live_order

 async def test_live_bootstrap_imports_full_asset_name_and_one_human(self):
  await self.admit_watch();before=self.server.match.snapshot();await self.request_watch()
  packets=[await self.read_watch()for _ in range(3)]
  info=decode(packets[0]).sections['WatchInfo']
  self.assertEqual(info['C'],'0|1');self.assertEqual(info['Q'],'0|0');self.assertEqual(info['G'],'0|0|1')
  self.assertTrue(all(len(bytes.fromhex(x))<=15 for x in info['CN'].split('|')))
  self.assertEqual(self.server.match.snapshot(),before)
  sys.path.insert(0,str(Path(__file__).parents[1]/'protocol_map'))
  from npc_observer_vectors import catalog
  from incoming_gameplay_vectors import ReceiveVM
  v=ReceiveVM(role=4);table,strings=catalog();v.w32(0x102bb630,0x20300000)
  v.u.mem_write(0x20300000,table);v.u.mem_write(0x20301000,strings)
  for payload in packets:v.queue(payload[5:].decode())
  self.assertEqual(v.receive(37),15);self.assertEqual(v.receive(38),1)
  self.assertEqual(v.text(v.r32(v.player+0x1b8)),'Flower Girl Natasha')
  self.assertEqual(v.r8(v.player+0xc7),1);self.assertEqual(v.r8(v.player+0x1d7),0)
  self.assertFalse(v.sends)

class NPCObserveImpossible(unittest.IsolatedAsyncioTestCase):
 observation_rule=1
 asyncSetUp=NPCObserverIntegration.asyncSetUp
 asyncTearDown=NPCObserverIntegration.asyncTearDown
 player=NPCObserverIntegration.player
 read_watch=NPCObserverIntegration.read_watch
 test_impossible=rules.ObserveDisabledIntegration.test_advertised_impossible_blocks_discovery_and_admission_without_disconnect

class NPCObserveNoComments(unittest.IsolatedAsyncioTestCase):
 observation_rule=2
 asyncSetUp=NPCObserverIntegration.asyncSetUp
 asyncTearDown=NPCObserverIntegration.asyncTearDown
 player=NPCObserverIntegration.player
 read_watch=NPCObserverIntegration.read_watch
 admit_watch=NPCObserverIntegration.admit_watch
 request_watch=NPCObserverIntegration.request_watch
 test_no_comments=rules.ObserveNoCommentsAdmission.test_no_comments_choice_still_allows_public_gameplay_observation

if __name__=='__main__':unittest.main()
