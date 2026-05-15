from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone


class SlidingWindowLimiter:
    def __init__(self, max_events: int, window_seconds: int) -> None:
        self.max_events = max_events
        self.window = timedelta(seconds=window_seconds)
        self._events: deque[datetime] = deque()

    def allow(self, at: datetime | None = None) -> bool:
        at = at or datetime.now(timezone.utc)
        self._trim(at)
        if len(self._events) >= self.max_events:
            return False
        self._events.append(at)
        return True

    def remaining(self, at: datetime | None = None) -> int:
        at = at or datetime.now(timezone.utc)
        self._trim(at)
        return max(0, self.max_events - len(self._events))

    def _trim(self, at: datetime) -> None:
        while self._events and at - self._events[0] > self.window:
            self._events.popleft()
