"""Safe environment defaults for tests collected outside an installation.

Database URLs are assigned, not defaulted. setdefault() would let an exported
DATABASE_URL or SANDBOX_DATABASE_URL win, so a developer or CI box with the
production values in its environment would run this suite against the real
databases - resetting funds and creating orders in live sandbox state. The
credentials below are still setdefault(), since those are only placeholders.

Test DB/log paths are further scoped to a fresh per-process tmp directory
(not a fixed `db/*-test.db` path) so that two sessions/processes running this
suite concurrently in the same checkout never race on the same physical
files. This module runs once at import time, before pytest fixtures exist and
before any `database/*.py` module reads DATABASE_URL via `os.getenv()` at its
own import time, so the tmp dir has to be created here rather than in a
fixture. The trade-off: `db/openalgo-test.db` etc. on disk are no longer
inspectable directly for debugging a failed run - the actual path is printed
below and also available via the TEST_DB_DIR env var during the run.
"""

import atexit
import os
import shutil
import tempfile

os.environ.setdefault("API_KEY_PEPPER", "0" * 64)
os.environ.setdefault("APP_KEY", "test-only-app-key")

# One fresh directory per pytest process - concurrent runs never share a file.
_TEST_DB_DIR = tempfile.mkdtemp(prefix="openalgo-test-db-")
os.environ["TEST_DB_DIR"] = _TEST_DB_DIR
atexit.register(shutil.rmtree, _TEST_DB_DIR, ignore_errors=True)
print(f"[conftest] isolated test DB dir: {_TEST_DB_DIR}")

# Assigned unconditionally: test isolation must not be overridable from the
# environment.
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB_DIR}/openalgo-test.db"
os.environ["SANDBOX_DATABASE_URL"] = f"sqlite:///{_TEST_DB_DIR}/sandbox-test.db"
os.environ["LOGS_DATABASE_URL"] = f"sqlite:///{_TEST_DB_DIR}/logs-test.db"
os.environ["LATENCY_DATABASE_URL"] = f"sqlite:///{_TEST_DB_DIR}/latency-test.db"

# Same isolation reasoning applies to logging: utils/logging.setup_logging()
# always writes ERROR+ entries to `{LOG_DIR}/errors.jsonl`, defaulting to the
# real `log/` directory. Without this, tests that deliberately exercise error
# paths (e.g. test_stock_simulator_control.test_stop_survives_sandbox_db_write_failure,
# which raises a synthetic "db unavailable" RuntimeError) log straight into
# the production error file that operators tail for real incidents, making a
# passing test look like a live outage. Scoped into the same per-process tmp
# dir for the same concurrent-session reason as the DB paths above.
os.environ["LOG_DIR"] = os.path.join(_TEST_DB_DIR, "log")
