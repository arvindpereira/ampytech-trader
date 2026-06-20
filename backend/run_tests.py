"""Isolated test runner.

Sets DATA_STORAGE_DIR to a throwaway temp directory BEFORE any application
module is imported, then runs unittest discovery. This guarantees the whole
suite uses a disposable database regardless of test import order, so tests can
never read from or mutate the real production database
(`backend/data/trading_system.db`).

Usage:
    python run_tests.py                # discover and run all tests
    python run_tests.py test_glide     # run specific module(s)
"""
import os
import sys
import tempfile
import unittest


def main():
    test_db_dir = tempfile.mkdtemp(prefix="ampy_test_db_")
    os.environ["DATA_STORAGE_DIR"] = test_db_dir
    os.environ["AMPY_TEST_DB_DIR"] = test_db_dir

    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, here)

    argv = sys.argv[1:]
    loader = unittest.TestLoader()
    if argv:
        suite = loader.loadTestsFromNames(argv)
    else:
        suite = loader.discover(start_dir=here, pattern="test_*.py")

    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
