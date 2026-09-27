"""Original Common344 opcode/body, not the unrelated file-list operation."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from profile_service import ProfileServer, checked
from test_profile_accounts import delete_request, file_request


class ProfileDeleteTests(unittest.TestCase):
    def test_original_core_delete_opcode_and_body_are_accepted(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'protocol_map'))
        from file_coverage_vectors import core_delete_request, core_delete_state
        request=core_delete_request();state=core_delete_state(2,1)
        self.assertTrue(request['match']);self.assertTrue(state['match'])
        _,cmd,sub,size=state['boundaries'][0]['args']
        self.assertEqual((cmd,sub,size),(3,2,0x198))
        # The fixture stops before the common checksum step.
        body=checked(bytes.fromhex(request['body_hex'])[:-4])
        deleted=[]
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            server=ProfileServer(Path(folder)/'profile.jsonl',SimpleNamespace(
                delete_profile_file=lambda key,path:deleted.append((key,path))))
            self.assertEqual(server.dispatch(cmd,sub,body),(b'',0))
        self.assertEqual(deleted[0][1],'U/g/TM0_RANKLIST')

    def test_wrong_opcode_or_body_extent_does_not_delete(self):
        deleted=[]
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            server=ProfileServer(Path(folder)/'profile.jsonl',SimpleNamespace(
                delete_profile_file=lambda key,path:deleted.append(path)))
            self.assertEqual(server.dispatch(3,3,delete_request('U/g/TM0_RANKLIST')),(b'',1))
            self.assertEqual(server.dispatch(3,2,file_request('U/g/TM0_RANKLIST')),(b'',1))
            self.assertEqual(deleted,[])
            self.assertEqual(server.dispatch(3,2,delete_request('./U/g/TM0_RANKLIST')),(b'',0))
            self.assertEqual(deleted,['U/g/TM0_RANKLIST'])


if __name__=='__main__':unittest.main()
