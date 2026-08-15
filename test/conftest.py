"""Safe environment defaults for tests collected outside an installation.

Database URLs are assigned, not defaulted. setdefault() would let an exported
DATABASE_URL or SANDBOX_DATABASE_URL win, so a developer or CI box with the
production values in its environment would run this suite against the real
databases - resetting funds and creating orders in live sandbox state. The
credentials below are still setdefault(), since those are only placeholders.
"""

import os

os.environ.setdefault("API_KEY_PEPPER", "0" * 64)
os.environ.setdefault("APP_KEY", "test-only-app-key")

# Assigned unconditionally: test isolation must not be overridable from the
# environment.
os.environ["DATABASE_URL"] = "sqlite:///db/openalgo-test.db"
os.environ["SANDBOX_DATABASE_URL"] = "sqlite:///db/sandbox-test.db"
os.environ["LOGS_DATABASE_URL"] = "sqlite:///db/logs-test.db"
os.environ["LATENCY_DATABASE_URL"] = "sqlite:///db/latency-test.db"

# Same isolation reasoning applies to logging: utils/logging.setup_logging()
# always writes ERROR+ entries to `{LOG_DIR}/errors.jsonl`, defaulting to the
# real `log/` directory. Without this, tests that deliberately exercise error
# paths (e.g. test_stock_simulator_control.test_stop_survives_sandbox_db_write_failure,
# which raises a synthetic "db unavailable" RuntimeError) log straight into
# the production error file that operators tail for real incidents, making a
# passing test look like a live outage.
os.environ["LOG_DIR"] = "log/test"
