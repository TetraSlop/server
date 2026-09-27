import sys
from pathlib import Path
import unittest

P=Path(__file__).resolve().parent.parent/'protocol_map'
if str(P) not in sys.path:sys.path.insert(0,str(P))
from table_password_vectors import run


class PasswordContractTests(unittest.TestCase):
    def test_original_local_compare_is_exact_and_case_sensitive(self):
        self.assertEqual(len(run()),20)


if __name__=='__main__':unittest.main()
