from __future__ import annotations

from douyin_mod_manager.senders.base import MessageSender


class MockMessageSender(MessageSender):
    def send(self, text: str) -> bool:
        self.sent.emit(text)
        return True
