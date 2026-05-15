from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from uuid import uuid4

from douyin_mod_manager.storage.database import Database


class SongStatus(StrEnum):
    PENDING = "pending"
    CURRENT = "current"
    DONE = "done"
    SKIPPED = "skipped"
    REJECTED = "rejected"


@dataclass(slots=True)
class SongRequest:
    session_id: str
    song_title: str
    requested_by: str
    status: SongStatus = SongStatus.PENDING
    note: str = ""
    id: str = ""


class SongRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def add(self, request: SongRequest) -> SongRequest:
        request.id = request.id or str(uuid4())
        now = datetime.now(timezone.utc).isoformat()
        with self.database.connect() as db:
            db.execute(
                """
                insert into song_requests
                (id, session_id, song_title, requested_by, status, note, created_at, updated_at)
                values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request.id,
                    request.session_id,
                    request.song_title,
                    request.requested_by,
                    request.status.value,
                    request.note,
                    now,
                    now,
                ),
            )
        return request

    def list_for_session(self, session_id: str) -> list[SongRequest]:
        with self.database.connect() as db:
            rows = db.execute(
                """
                select * from song_requests
                where session_id = ?
                order by created_at asc
                """,
                (session_id,),
            ).fetchall()
        return [
            SongRequest(
                id=row["id"],
                session_id=row["session_id"],
                song_title=row["song_title"],
                requested_by=row["requested_by"],
                status=SongStatus(row["status"]),
                note=row["note"] or "",
            )
            for row in rows
        ]

    def update_status(self, request_id: str, status: SongStatus) -> None:
        with self.database.connect() as db:
            db.execute(
                "update song_requests set status = ?, updated_at = ? where id = ?",
                (status.value, datetime.now(timezone.utc).isoformat(), request_id),
            )
