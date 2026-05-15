from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from douyin_mod_manager.core.events import LiveMode


@dataclass(slots=True)
class LiveSession:
    name: str
    mode: LiveMode = LiveMode.SINGING
    room_note: str = ""
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    ended_at: datetime | None = None
    id: str = field(default_factory=lambda: str(uuid4()))

    @property
    def active(self) -> bool:
        return self.ended_at is None

    def end(self) -> None:
        self.ended_at = datetime.now(timezone.utc)
