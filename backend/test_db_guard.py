"""Safety guard shared by tests that delete/seed rows.

Guarantees a test will refuse to run against the real production database instead
of silently corrupting it (which previously leaked a fake crash-risk snapshot and
wiped the macro_indicators table). Works under any test runner, not just pytest.
"""
import os


def assert_isolated_db():
    """Raise unless the bound DB engine points at a throwaway/temp database."""
    from app.database.connection import engine
    db_path = (engine.url.database or "").lower()

    tmp = os.path.realpath(os.environ.get("TMPDIR", "/tmp")).lower()
    looks_temp = (
        "ampy_test_db_" in db_path
        or db_path.startswith(tmp)
        or db_path.startswith("/tmp/")
        or db_path.startswith("/var/folders/")  # macOS tempdir
        or db_path in (":memory:", "")
    )
    if not looks_temp:
        raise RuntimeError(
            "Refusing to run a destructive test against a non-isolated database: "
            f"{db_path!r}. Run tests via pytest (which loads conftest.py first) so "
            "DATA_STORAGE_DIR is set to a temp dir before app modules are imported."
        )
