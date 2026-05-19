from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class DomSelectorConfig:
    event_containers: list[str] = field(
        default_factory=lambda: [
            "[data-dmm-event]",
            ".webcast-chatroom___item",
            ".chat-item",
            ".comment-item",
            ".message-item",
        ]
    )
    username_selectors: list[str] = field(
        default_factory=lambda: [
            "[data-dmm-username]",
            ".username",
            ".user-name",
            ".nickname",
            ".name",
        ]
    )
    content_selectors: list[str] = field(
        default_factory=lambda: [
            "[data-dmm-content]",
            ".content",
            ".message",
            ".comment",
            ".text",
        ]
    )
    chat_input_selectors: list[str] = field(
        default_factory=lambda: [
            "[data-dmm-chat-input]",
            "textarea",
            "input[type='text']",
            "[contenteditable='true']",
        ]
    )
    send_button_selectors: list[str] = field(
        default_factory=lambda: [
            "[data-dmm-send]",
            "button[type='submit']",
            "button.send",
            ".send-button",
        ]
    )

    @classmethod
    def load(cls, path: Path | None = None) -> "DomSelectorConfig":
        if path is None:
            path = Path(__file__).resolve().parents[2] / "config" / "dom_selectors.json"
        config = cls()
        if not path.exists():
            return config
        data = json.loads(path.read_text(encoding="utf-8"))
        for key, value in data.items():
            if hasattr(config, key) and isinstance(value, list):
                setattr(config, key, [str(item) for item in value])
        return config

    def to_javascript_object(self) -> str:
        return json.dumps(
            {
                "eventContainers": self.event_containers,
                "usernameSelectors": self.username_selectors,
                "contentSelectors": self.content_selectors,
                "chatInputSelectors": self.chat_input_selectors,
                "sendButtonSelectors": self.send_button_selectors,
            },
            ensure_ascii=False,
        )
