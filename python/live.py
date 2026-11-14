"""Live state for the web page: what is being typed right now + a conversation log.

The page polls GET /api/live?since=<last id> a few times per second and gets the current
state plus only the log entries it hasn't seen yet. (Polling instead of WebSockets: the
socket.io client would have to come from the internet, the device should work offline.)
"""

import threading
import time
from collections import deque

MAX_ENTRIES = 200


class LiveState:
    def __init__(self):
        self._lock = threading.Lock()
        self._next_id = 1
        self._entries = deque(maxlen=MAX_ENTRIES)
        self.current = {
            "typing": "",          # finished letters/words of the sentence being typed
            "symbol": "",          # dots/dashes of the letter being typed
            "key_down": False,
            "calibrating": False,
            "calibration": [0, 0], # presses done, presses needed
            "listening": False,
            "wpm": 15,
        }

    def update(self, **fields):
        """Change fields of the current state (typing, symbol, listening, wpm, ...)."""
        with self._lock:
            self.current.update(fields)

    def add(self, kind, **data):
        """Append a log entry. kind: 'you' (user's sentence), 'partner' (heard speech), 'info'."""
        with self._lock:
            entry = {"id": self._next_id, "time": time.time(), "kind": kind, **data}
            self._next_id += 1
            self._entries.append(entry)
            return entry["id"]

    def snapshot(self, since=0):
        with self._lock:
            return {
                "current": dict(self.current),
                "entries": [e for e in self._entries if e["id"] > since],
                "last_id": self._next_id - 1,
            }
