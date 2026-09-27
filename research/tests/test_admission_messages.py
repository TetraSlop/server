"""Business rejections select original nonfatal and appropriately labeled paths."""
from pathlib import Path
import sys,unittest

class AdmissionMessages(unittest.TestCase):
 def test_actual_replies_reach_original_localized_failure_labels(self):
  sys.path.insert(0,str(Path(__file__).parents[1]/'protocol_map'))
  from admission_error_vectors import cases
  self.assertEqual(len(cases()),17)

if __name__=='__main__':unittest.main()
