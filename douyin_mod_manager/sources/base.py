from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from douyin_mod_manager.core.events import LiveEvent


class EventSource(QObject):
    event_received = Signal(object)
    status_changed = Signal(str)

    def start(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError
