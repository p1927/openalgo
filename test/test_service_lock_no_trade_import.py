"""service_lock runs before OpenAlgo binds its port, in a venv without `tradingagents`: it must not import
`trade_integrations` and must honour VIBE_TRADING_HOME."""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_lock_dir_honours_home_without_trade_integrations():
    with tempfile.TemporaryDirectory() as home:
        code = (
            "import sys; sys.modules['trade_integrations'] = None; sys.modules['tradingagents'] = None;"
            f"sys.path.insert(0, {str(ROOT)!r}); from utils.service_lock import _lock_dir; print(_lock_dir())"
        )
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env={"VIBE_TRADING_HOME": home, "PATH": ""})
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == str(Path(home) / "locks")
