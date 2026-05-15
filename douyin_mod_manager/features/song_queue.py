from __future__ import annotations

import re

from douyin_mod_manager.core.events import EventType, LiveEvent
from douyin_mod_manager.storage.repositories import SongRepository, SongRequest


SONG_REQUEST_RE = re.compile(r"^点歌\s+(?P<title>.+)$")


class SongQueueService:
    def __init__(self, repository: SongRepository) -> None:
        self.repository = repository
        self.enabled = True

    def maybe_add_from_event(self, event: LiveEvent) -> SongRequest | None:
        if not self.enabled or event.type != EventType.CHAT or not event.content:
            return None
        match = SONG_REQUEST_RE.match(event.content.strip())
        if not match:
            return None
        title = match.group("title").strip()
        if not title:
            return None
        return self.repository.add(
            SongRequest(
                session_id=event.session_id,
                song_title=title,
                requested_by=event.display_user,
            )
        )
