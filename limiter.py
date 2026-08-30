# limiter.py

import threading

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from limits.storage.memory import MemoryStorage

# limits==5.8.0's MemoryStorage.__schedule_expiry does an unlocked check-then-act on
# self.timer (is_alive() then replace-and-start): two threads can both see a dead timer,
# both create a fresh Timer, and both end up calling .start() on the same object once the
# second assignment races ahead of the first thread's start() call, raising "threads can
# only be started once" and 500ing the request. No newer release fixes this (5.8.0 is
# latest). Serialize it with a lock until upstream does.
_schedule_expiry_lock = threading.Lock()
_orig_schedule_expiry = MemoryStorage._MemoryStorage__schedule_expiry


def _locked_schedule_expiry(self):
    with _schedule_expiry_lock:
        _orig_schedule_expiry(self)


MemoryStorage._MemoryStorage__schedule_expiry = _locked_schedule_expiry

# Initialize Flask-Limiter without the app object
limiter = Limiter(key_func=get_remote_address, storage_uri="memory://", strategy="moving-window")
