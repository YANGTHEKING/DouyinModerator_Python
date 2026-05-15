from __future__ import annotations

from dataclasses import dataclass

from douyin_mod_manager.core.events import EventType, LiveEvent


@dataclass(slots=True)
class RiskHit:
    label: str
    reason: str


class RiskDetector:
    def __init__(self) -> None:
        self.keywords = {
            "带节奏": "疑似带节奏",
            "中之人": "现实身份试探",
            "真人": "现实身份试探",
            "骂": "不文明表达",
            "滚": "不文明表达",
        }
        self._recent_by_user: dict[str, list[str]] = {}

    def detect(self, event: LiveEvent) -> list[RiskHit]:
        if event.type != EventType.CHAT or not event.content:
            return []
        hits = [
            RiskHit(label, f"命中词：{keyword}")
            for keyword, label in self.keywords.items()
            if keyword in event.content
        ]
        user = event.username or event.user_id
        if user:
            messages = self._recent_by_user.setdefault(user, [])
            messages.append(event.content)
            del messages[:-5]
            if len(messages) >= 3 and len(set(messages[-3:])) == 1:
                hits.append(RiskHit("刷屏", "同一用户连续重复发言"))
        return hits
