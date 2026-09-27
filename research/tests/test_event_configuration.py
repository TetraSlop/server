"""Explicit event configuration reaches the same validated wire/file builders."""
import copy
from dataclasses import replace
import unittest
from event_configuration import parse_event_configs,parse_event_setup
from event_rooms import EventRooms
from event_service import EventHost,PrizeDisplay,event_data
from economy_service import EconomyService
from identities import game_id


DOCUMENT={'events':[{'id':'festival','listing':{'key_region':'Local Lobby','key_event':'Festival','description':'Local fixture','scalar':4096},
    'data':{'classes':['First','Next'],'steps_per_class':4,'missions':{'0':3},'prizes':[{'cards':[5,9],'money':250,'description':'Local award display'}]},
    'start':100,'end':500,'room_raw':4096,'init_raw':4097,'card_raw':4098,'card_service_enabled':True}]}


class EventConfigurationTests(unittest.TestCase):
    def test_distinct_tables_can_share_one_enabled_event(self):
        item=copy.deepcopy(DOCUMENT);item['events'][0]['match_enabled']=True
        item['tables']=[dict(event_id='festival',table_raw=4099+i,channel=f'#EVTABLE00{i+1}')for i in range(2)]
        _,tables,_=parse_event_setup(item)
        self.assertEqual([t.event_id for t in tables],['festival','festival'])
        self.assertEqual(len({t.table_id for t in tables}),2)

    def test_match_setup_requires_an_explicit_unique_table_and_policy(self):
        item=copy.deepcopy(DOCUMENT);item['events'][0]['match_enabled']=True
        with self.assertRaises(ValueError):parse_event_setup(item)
        table=dict(event_id='festival',table_raw=4099,channel='#EVTABLE001',player_count=3)
        item['tables']=[table];item['progress_policy']='wins'
        events,tables,policy=parse_event_setup(item)
        self.assertTrue(events[0].match_enabled)
        self.assertEqual((tables[0].table_id,tables[0].player_count,policy),(game_id(4099),3,'wins'))
        for key,value in (('table_raw',True),('seed',-1),('cpu_cards',[[1]*7]*5),('start_fields',[0]*16)):
            invalid=copy.deepcopy(item);invalid['tables'][0][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):parse_event_setup(invalid)
        item['tables']*=2
        with self.assertRaises(ValueError):parse_event_setup(item)

    def test_json_values_project_without_assigning_opaque_fields(self):
        configs=parse_event_configs(DOCUMENT);c=configs[0]
        self.assertEqual(c.room_id,game_id(4096))
        self.assertEqual(c.data,event_data(classes=(b'First',b'Next'),steps_per_class=4,missions={0:3},
            prizes=(PrizeDisplay((5,9),250,b'Local award display'),)))
        rooms=EventRooms(EventHost(EconomyService(),configs))
        self.assertEqual(len(rooms.rooms),1)
        self.assertEqual(len(c.service_records()),1)
        self.assertEqual(c.service_records()[0][0x28],ord('F'))
        self.assertEqual(parse_event_configs({'events':[]}),())

    def test_malformed_unknown_and_unrepresentable_settings_fail(self):
        invalid=[]
        for field,value in (('room_raw',True),('room_raw',1<<41),('surprise',1),('init_channel','long channel with spaces')):
            item=copy.deepcopy(DOCUMENT);item['events'][0][field]=value;invalid.append(item)
        item=copy.deepcopy(DOCUMENT);item['events'][0]['data']['missions']={'8':2};invalid.append(item)
        item=copy.deepcopy(DOCUMENT);item['events'][0]['listing']['key_event']='雪';invalid.append(item)
        invalid.extend(({'events':'text'},{'events':[],'unknown':1},[]))
        for document in invalid:
            with self.subTest(document=document),self.assertRaises((ValueError,TypeError)):
                parse_event_configs(document)


if __name__=='__main__':unittest.main()
