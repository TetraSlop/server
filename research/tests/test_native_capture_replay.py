"""Replay unchanged application commands from the user's completed CPU match.

Fresh encrypted authentication replaces only the expired connection handshake.
Archived profile supplies the original inventory. Replay ends after selection;
later moves depend on the historical server's older AI and random policy.
"""
import asyncio
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest

from economy_service import Card,PROFILE_SIZE
from identities import player_id
from match_service import decode
from profile_service import checksum
from server import Server
from test_server import Client

CAPTURE=Path(__file__).parent/'native_runs/20260926_user_cpu_complete'
HASHES={'traffic.jsonl':'2a9be3548d0f2a25585aa7a78b599ff6462acdfb8ac020b104d772d3e8fa79ce',
        'profile.jsonl':'0a99c40d2baa63557afa72d3ccd8b7861df10794b76dfda461b1c93688d1e725'}


def captured_rows(name):
    raw=(CAPTURE/name).read_bytes()
    assert hashlib.sha256(raw).hexdigest()==HASHES[name]
    return [json.loads(line)for line in raw.decode().splitlines()]


def captured_profile():
    rows=captured_rows('profile.jsonl')
    index=next(i for i,r in enumerate(rows)if r.get('name')=='U/g/TM0DataFile')
    record=rows[index+1]
    assert record['event']=='response' and record['error']==0
    data=bytes.fromhex(record['body'])
    assert len(data)==PROFILE_SIZE+4 and checksum(data[:-4])==int.from_bytes(data[-4:],'little')
    return data[:-4]


def captured_start():
    rows=captured_rows('traffic.jsonl')
    session=next(r['session']for r in rows if r['event']=='receive'and '@GameEN='in r.get('text',''))
    commands=[]
    for row in rows:
        text=row.get('text','')
        if row.get('session')!=session or row['event']!='receive' or not text.startswith(('NOTICE ','JOIN ','PART ')):continue
        commands.append(text.encode('latin1'))
        if '@CardSelect='in text:break
    assert b'/HID=0/'in next(c for c in commands if b'@GameEN='in c)
    assert b'/HID=0/'in next(c for c in commands if b'@Req='in c)
    return commands


class NativeCaptureReplayTests(unittest.IsolatedAsyncioTestCase):
    async def test_recorded_native_room_table_chat_and_selection(self):
        asyncio.get_running_loop().slow_callback_duration=1
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent)as folder:
            server=Server(state_path=Path(folder)/'state.json')
            client=Client();profile=captured_profile();key=next(iter(server.accounts)).decode()
            count=struct.unpack_from('<H',profile,0x3c)[0]
            cards=[Card(*struct.unpack_from('<H7B3x',profile,0x148+12*i))for i in range(count)]
            server.economy.provision(key,nn=player_id(key.encode()),player_id=1,name=b'LocalPlayer',
                guild=profile[0x3b],balance=struct.unpack_from('<i',profile,0x34)[0],cards=cards,profile_template=profile)
            server.account_keys.add(key)
            try:
                await client.open(await server.start(0));self.assertIn(b' 422 ',await client.read())
                received=[];commands=captured_start()
                for index,command in enumerate(commands):
                    await client.send(command);await client.send(b'PING :captured-barrier')
                    for _ in range(150):
                        line=await client.read()
                        self.assertTrue(line,f'client disconnected on captured application command {index}')
                        if line==b'PONG captured-barrier':break
                        if b' :GTM0G'in line:received.append(decode(line.split(b' :',1)[1]))
                    else:self.fail('capture replay response limit')
                self.assertEqual(server.match.phase,'turn')
                self.assertEqual(server.match.players[0].character_id,1)
                self.assertEqual(len(server.match.players[0].hand),5)
                self.assertTrue(any(r.primary==2 and r.sections['PLAYACK']['EN']=='1'for r in received))
                self.assertTrue(any(r.primary==0x43 and r.subtype==8 for r in received))
                self.assertEqual(len(server.match_reservations),1)
            finally:
                if hasattr(client,'writer'):await client.close()
                await server.stop()


if __name__=='__main__':unittest.main()
