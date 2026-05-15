from __future__ import annotations

import random

from PySide6.QtCore import QTimer

from douyin_mod_manager.core.events import EventType, LiveEvent
from douyin_mod_manager.sources.base import EventSource


class MockEventSource(EventSource):
    def __init__(self, session_id: str) -> None:
        super().__init__()
        self.session_id = session_id
        self.timer = QTimer(self)
        self.timer.setInterval(1300)
        self.timer.timeout.connect(self.emit_random)
        self.users = ["小葵", "阿澈", "星野", "Mika", "路过听歌", "今天也想点歌"]
        self.songs = ["稻香", "夜空中最亮的星", "群青", "光るなら", "起风了"]

    def start(self) -> None:
        self.timer.start()
        self.status_changed.emit("模拟源运行中")

    def stop(self) -> None:
        self.timer.stop()
        self.status_changed.emit("模拟源已停止")

    def emit_random(self) -> None:
        username = random.choice(self.users)
        choice = random.choices(
            [EventType.CHAT, EventType.USER_ENTER, EventType.GIFT, EventType.FOLLOW, EventType.SYSTEM],
            weights=[62, 13, 12, 8, 5],
        )[0]
        if choice == EventType.CHAT:
            content = random.choice(
                [
                    "主播唱得好稳",
                    f"点歌 {random.choice(self.songs)}",
                    "下一首是什么",
                    "不要带节奏，听歌就好",
                    "PK 快开始了吗",
                    "今天杂谈好有意思",
                ]
            )
            event = LiveEvent(choice, self.session_id, username=username, content=content)
        elif choice == EventType.GIFT:
            gift = random.choice(["小心心", "粉丝灯牌", "棒棒糖", "为你打call"])
            count = random.randint(1, 6)
            event = LiveEvent(
                choice,
                self.session_id,
                username=username,
                raw={"gift_name": gift, "gift_count": count, "gift_value": count},
            )
        elif choice == EventType.USER_ENTER:
            event = LiveEvent(choice, self.session_id, username=username)
        elif choice == EventType.FOLLOW:
            event = LiveEvent(choice, self.session_id, username=username)
        else:
            event = LiveEvent(choice, self.session_id, content="直播间状态正常", raw={"message": "直播间状态正常"})
        self.event_received.emit(event)
