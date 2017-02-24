import unittest
import os

from test.test_support import run_unittest, import_module, universal_crt
#Skip tests if _ctypes module does not exist
import_module('_ctypes')
if 'APPVEYOR' in os.environ and universal_crt:
    raise unittest.SkipTest(
        'ctypes test is crashing on APPVEYOR with universal CRT')


def test_main():
    import ctypes.test
    skipped, testcases = ctypes.test.get_tests(ctypes.test, "test_*.py", verbosity=0)
    suites = [unittest.makeSuite(t) for t in testcases]
    run_unittest(unittest.TestSuite(suites))

if __name__ == "__main__":
    test_main()
