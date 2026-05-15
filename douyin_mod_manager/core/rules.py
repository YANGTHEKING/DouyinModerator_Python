from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from uuid import uuid4

from douyin_mod_manager.core.events import ActionProposal, EventType, LiveEvent, LiveMode


class ConditionOperator(StrEnum):
    CONTAINS = "contains"
    REGEX = "regex"
    EQUALS = "equals"
    MIN_VALUE = "min_value"


@dataclass(slots=True)
class Condition:
    field: str
    operator: ConditionOperator
    value: str

    def matches(self, event: LiveEvent) -> bool:
        actual = self._resolve(event)
        if actual is None:
            return False
        actual_text = str(actual)
        if self.operator == ConditionOperator.CONTAINS:
            return self.value.lower() in actual_text.lower()
        if self.operator == ConditionOperator.REGEX:
            return re.search(self.value, actual_text, re.IGNORECASE) is not None
        if self.operator == ConditionOperator.EQUALS:
            return actual_text == self.value
        if self.operator == ConditionOperator.MIN_VALUE:
            try:
                return float(actual) >= float(self.value)
            except (TypeError, ValueError):
                return False
        return False

    def _resolve(self, event: LiveEvent) -> object | None:
        if hasattr(event, self.field):
            return getattr(event, self.field)
        return event.raw.get(self.field)


@dataclass(slots=True)
class Rule:
    name: str
    event_type: EventType
    response_template: str
    conditions: list[Condition] = field(default_factory=list)
    enabled: bool = True
    modes: set[LiveMode] = field(default_factory=lambda: set(LiveMode))
    auto_send: bool = False
    low_risk: bool = False
    cooldown_seconds: int = 30
    max_per_session: int = 50
    id: str = field(default_factory=lambda: str(uuid4()))
    _last_triggered_at: datetime | None = None
    _session_count: int = 0

    def evaluate(self, event: LiveEvent, mode: LiveMode) -> ActionProposal | None:
        if not self.enabled or event.type != self.event_type or mode not in self.modes:
            return None
        if self._session_count >= self.max_per_session:
            return None
        if self._last_triggered_at is not None:
            elapsed = datetime.now(timezone.utc) - self._last_triggered_at
            if elapsed < timedelta(seconds=self.cooldown_seconds):
                return None
        if not all(condition.matches(event) for condition in self.conditions):
            return None

        self._last_triggered_at = datetime.now(timezone.utc)
        self._session_count += 1
        return ActionProposal(
            event_id=event.id,
            rule_id=self.id,
            rule_name=self.name,
            text=self._render(event, mode),
            auto_send=self.auto_send and self.low_risk,
        )

    def _render(self, event: LiveEvent, mode: LiveMode) -> str:
        values = {
            "username": event.display_user,
            "content": event.content or "",
            "gift_name": event.raw.get("gift_name", ""),
            "gift_count": event.raw.get("gift_count", ""),
            "current_song": event.raw.get("current_song", ""),
            "mode": mode.value,
        }
        try:
            return self.response_template.format(**values)
        except KeyError:
            return self.response_template


class RuleEngine:
    def __init__(self, rules: list[Rule] | None = None) -> None:
        self.rules = rules or default_rules()

    def evaluate(self, event: LiveEvent, mode: LiveMode) -> list[ActionProposal]:
        return [proposal for rule in self.rules if (proposal := rule.evaluate(event, mode))]


def default_rules() -> list[Rule]:
    return [
        Rule(
            name="点歌请求",
            event_type=EventType.CHAT,
            conditions=[Condition("content", ConditionOperator.REGEX, r"^点歌\s+.+")],
            response_template="收到 {username} 的点歌请求，房管确认后加入歌单。",
            modes={LiveMode.SINGING},
            auto_send=False,
            low_risk=True,
            cooldown_seconds=5,
        ),
        Rule(
            name="感谢关注",
            event_type=EventType.FOLLOW,
            response_template="谢谢 {username} 的关注，欢迎来听歌。",
            modes={LiveMode.SINGING, LiveMode.TALK},
            auto_send=True,
            low_risk=True,
            cooldown_seconds=45,
        ),
        Rule(
            name="礼物感谢草稿",
            event_type=EventType.GIFT,
            response_template="谢谢 {username} 的 {gift_name} x{gift_count}，心意收到啦。",
            modes={LiveMode.SINGING, LiveMode.TALK, LiveMode.PK},
            auto_send=False,
            low_risk=True,
            cooldown_seconds=15,
        ),
        Rule(
            name="文明提醒",
            event_type=EventType.CHAT,
            conditions=[Condition("content", ConditionOperator.REGEX, r"骂|滚|破防|带节奏")],
            response_template="请大家文明交流，专注直播内容。",
            modes={LiveMode.SINGING, LiveMode.TALK, LiveMode.PK},
            auto_send=False,
            low_risk=False,
            cooldown_seconds=30,
        ),
    ]
