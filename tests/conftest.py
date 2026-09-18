"""Test configuration.

These assignments run at import time, before any test module imports `app`,
because the whole suite must never touch a real database — not in CI, and not
on a developer laptop that has working credentials and an open tunnel.
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ["DB_ENABLED"] = "0"  # the one mode that opens no socket at all
os.environ["DB_DOTENV"] = "0"  # ignore the bundled config/defaults.env
os.environ["DB_ENV_FILE"] = str(Path(tempfile.gettempdir()) / "pataterno-no-such-file.env")
os.environ["DBCHECK_ON_STARTUP"] = "0"
os.environ.pop("DB_PASSWORD", None)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest  # noqa: E402

import dbconfig  # noqa: E402


@pytest.fixture
def dbcfg():
    """Re-resolve configuration around a test that changes the environment."""
    dbconfig.reload_config()
    yield dbconfig
    dbconfig.reload_config()
