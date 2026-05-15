from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class MessageSender(QObject):
    sent = Signal(str)
    failed = Signal(str)

    def send(self, text: str) -> bool:
        raise NotImplementedError
