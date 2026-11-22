import json, sys, unittest
import oldbackend  # noqa: F401  patches the runtime
from tools.check_tests import test_modules
mods = [m for m in test_modules("native") if m not in sys.argv[1:]]
suite = unittest.TestLoader().loadTestsFromNames(mods)
r = unittest.TextTestRunner(verbosity=1).run(suite)
print(json.dumps({"run": r.testsRun, "failures": len(r.failures), "errors": len(r.errors), "skipped": len(r.skipped)}))
for t, tb in r.failures + r.errors:
    print("FAIL", t.id(), tb.strip().splitlines()[-1][:300])
