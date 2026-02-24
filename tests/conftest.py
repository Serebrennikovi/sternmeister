"""Set required env vars before any server imports."""
import os
import tempfile

import pytest

# Use a unique temp file for each test session — auto-cleaned by OS.
_test_db_fd, _test_db_path = tempfile.mkstemp(suffix=".db", prefix="sternmeister_test_")
os.close(_test_db_fd)

os.environ.setdefault("KOMMO_DOMAIN", "test.kommo.com")
os.environ.setdefault("KOMMO_TOKEN", "test-token")
os.environ.setdefault("WAZZUP_API_KEY", "test-api-key")
os.environ.setdefault("WAZZUP_CHANNEL_ID", "test-channel-id")
os.environ.setdefault("WAZZUP_TEMPLATE_ID", "test-template-id")
os.environ["DATABASE_PATH"] = _test_db_path


@pytest.fixture(autouse=True, scope="session")
def _cleanup_test_db():
    """Remove the temp DB file after all tests finish."""
    yield
    try:
        os.unlink(_test_db_path)
    except FileNotFoundError:
        pass
