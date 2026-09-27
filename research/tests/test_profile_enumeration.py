"""Native startup mailbox enumeration and shutdown with open profile sockets."""
import asyncio
import json
from pathlib import Path
import struct
import sys
import unittest

import test_profile as profile_tests
from profile_service import checked


class ProfileEnumerationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=profile_tests.ProfileTests.asyncSetUp
    asyncTearDown=profile_tests.ProfileTests.asyncTearDown
    transact=profile_tests.ProfileTests.transact

    def request(self,*,owner=1,name=b'O/m/',maximum=100):
        body=bytearray(0x19c)
        struct.pack_into('<Q',body,8,owner)
        body[16:16+len(name)+1]=name+b'\0'
        struct.pack_into('<H',body,0x190,maximum)
        return checked(body)

    async def test_empty_mailbox_includes_original_message_list_trailer(self):
        for maximum in (0,1,100,65535):
            self.assertEqual(await self.transact(3,3,self.request(maximum=maximum),fragmented=True),(0,checked(bytes(8))))

    async def test_native_capture_ignores_uninitialized_request_padding(self):
        path=Path(__file__).with_name('integration_profile.jsonl')
        rows=[r for line in path.read_text().splitlines()if (r:=json.loads(line)).get('event')=='file_request'and r.get('operation')==3]
        self.assertTrue(rows)
        for row in rows[:2]:
            self.assertEqual(await self.transact(3,3,bytes.fromhex(row['body'])),(0,checked(bytes(8))))

    async def test_owner_other_paths_bad_shapes_and_corruption_do_not_succeed(self):
        for body in (self.request(owner=2),self.request(owner=1|(1<<41)),self.request(name=b'U/g/'),self.request(name=b'O/m/../')):
            self.assertEqual(await self.transact(3,3,body),(0x7e,b''))
        self.assertEqual(await self.transact(3,3,checked(bytes(0x194))),(1,b''))
        with self.assertRaises(asyncio.IncompleteReadError):
            await self.transact(3,3,self.request()[:-1]+b'\xff')

    async def test_shutdown_closes_open_transport_before_waiting_for_listener(self):
        reader,writer=await asyncio.open_connection('127.0.0.1',self.port)
        try:
            opening=bytearray(40);opening[4]=1
            writer.write(opening);await writer.drain()
            await reader.readexactly(24)
            self.assertTrue(self.server.tasks)
            await asyncio.wait_for(self.server.stop(),2)
            self.assertEqual(await reader.read(),b'')
            self.assertFalse(self.server.tasks);self.assertFalse(self.server.writers)
        finally:
            writer.close();await writer.wait_closed()


class OriginalEnumerationTests(unittest.TestCase):
    def test_original_count_file_and_message_list_consumers(self):
        sys.path.insert(0,str(Path(__file__).parents[1]/'protocol_map'))
        from profile_enumeration_vectors import cases
        self.assertEqual(len(cases()),6)


if __name__=='__main__':unittest.main()
