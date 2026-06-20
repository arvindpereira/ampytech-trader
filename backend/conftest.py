"""Pytest bootstrap: force an isolated, throwaway database for the whole test
session BEFORE any application module is imported.

`app.core.config` resolves `DB_PATH` from `DATA_STORAGE_DIR` at import time and
`app.database.connection` binds the SQLAlchemy engine to it immediately. If a
test module sets `DATA_STORAGE_DIR` only in its own header, any earlier-imported
module will already have bound the engine to the real production database
(`backend/data/trading_system.db`) — so tests would mutate (and previously
wiped) production data. pytest imports this conftest before collecting any test
module, guaranteeing the env var is set first.
"""
import os
import tempfile

if not os.environ.get("AMPY_TEST_DB_DIR"):
    _test_db_dir = tempfile.mkdtemp(prefix="ampy_test_db_")
    os.environ["AMPY_TEST_DB_DIR"] = _test_db_dir
    os.environ["DATA_STORAGE_DIR"] = _test_db_dir
