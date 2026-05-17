from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from uuid import uuid4


class EventType(StrEnum):
    CHAT = "chat"
    USER_ENTER = "user_enter"
    GIFT = "gift"
    FOLLOW = "follow"
    SYSTEM = "system"


class LiveMode(StrEnum):
    SINGING = "singing"
    TALK = "talk"
    PK = "pk"


MODE_LABELS = {
    LiveMode.SINGING: "歌回",
    LiveMode.TALK: "杂谈",
    LiveMode.PK: "PK",
}

EVENT_LABELS = {
    EventType.CHAT: "弹幕",
    EventType.USER_ENTER: "进场",
    EventType.GIFT: "礼物",
    EventType.FOLLOW: "关注",
    EventType.SYSTEM: "系统",
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class LiveEvent:
    type: EventType
    session_id: str
    room_id: str | None = None
    user_id: str | None = None
    username: str | None = None
    content: str | None = None
    raw: dict = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=now_utc)
    received_at: datetime = field(default_factory=now_utc)
    source: str = "mock"
    id: str = field(default_factory=lambda: str(uuid4()))

    @property
    def label(self) -> str:
        return EVENT_LABELS.get(self.type, self.type.value)

    @property
    def display_user(self) -> str:
        if self.type == EventType.SYSTEM:
            return self.username or "系统"
        return self.username or "匿名用户"

    @property
    def display_content(self) -> str:
        if self.type == EventType.GIFT:
            name = self.raw.get("gift_name", "礼物")
            count = self.raw.get("gift_count", 1)
            return f"送出 {name} x{count}"
        if self.type == EventType.USER_ENTER:
            return "进入直播间"
        if self.type == EventType.FOLLOW:
            return "关注了主播"
        return self.content or self.raw.get("message", "")


@dataclass(slots=True)
class ActionProposal:
    event_id: str
    rule_id: str
    rule_name: str
    text: str
    auto_send: bool = False
    created_at: datetime = field(default_factory=now_utc)
    id: str = field(default_factory=lambda: str(uuid4()))
