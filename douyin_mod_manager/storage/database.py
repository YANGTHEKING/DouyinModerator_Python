from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from douyin_mod_manager.core.events import ActionProposal, LiveEvent
from douyin_mod_manager.core.sessions import LiveSession


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def default(cls) -> "Database":
        root = Path(__file__).resolve().parents[2]
        data_dir = root / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        return cls(data_dir / "app.db")

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                create table if not exists sessions (
                  id text primary key,
                  name text not null,
                  mode text not null,
                  room_note text,
                  started_at text not null,
                  ended_at text
                );

                create table if not exists events (
                  id text primary key,
                  session_id text not null,
                  type text not null,
                  username text,
                  content text,
                  source text not null,
                  raw_json text not null,
                  occurred_at text not null,
                  received_at text not null
                );

                create table if not exists actions (
                  id text primary key,
                  session_id text not null,
                  event_id text not null,
                  rule_id text not null,
                  rule_name text not null,
                  text text not null,
                  auto_send integer not null,
                  sent integer not null default 0,
                  created_at text not null,
                  sent_at text
                );

                create table if not exists song_requests (
                  id text primary key,
                  session_id text not null,
                  song_title text not null,
                  requested_by text not null,
                  status text not null,
                  note text,
                  created_at text not null,
                  updated_at text not null
                );
                """
            )

    def save_session(self, session: LiveSession) -> None:
        with self.connect() as db:
            db.execute(
                """
                insert or replace into sessions
                (id, name, mode, room_note, started_at, ended_at)
                values (?, ?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    session.name,
                    session.mode.value,
                    session.room_note,
                    _dt(session.started_at),
                    _dt(session.ended_at),
                ),
            )

    def save_event(self, event: LiveEvent) -> None:
        with self.connect() as db:
            db.execute(
                """
                insert or ignore into events
                (id, session_id, type, username, content, source, raw_json, occurred_at, received_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.id,
                    event.session_id,
                    event.type.value,
                    event.username,
                    event.content,
                    event.source,
                    json.dumps(event.raw, ensure_ascii=False),
                    _dt(event.occurred_at),
                    _dt(event.received_at),
                ),
            )

    def save_action(self, session_id: str, action: ActionProposal, sent: bool = False) -> None:
        with self.connect() as db:
            db.execute(
                """
                insert or replace into actions
                (id, session_id, event_id, rule_id, rule_name, text, auto_send, sent, created_at, sent_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    action.id,
                    session_id,
                    action.event_id,
                    action.rule_id,
                    action.rule_name,
                    action.text,
                    int(action.auto_send),
                    int(sent),
                    _dt(action.created_at),
                    _dt(datetime.now().astimezone()) if sent else None,
                ),
            )

    def recent_events(self, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "select * from events order by received_at desc limit ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def clear_logs(self) -> None:
        with self.connect() as db:
            db.execute("delete from actions")
            db.execute("delete from events")


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
