from __future__ import annotations

import json

from PySide6.QtWebEngineCore import QWebEnginePage

from douyin_mod_manager.senders.base import MessageSender
from douyin_mod_manager.sources.dom_config import DomSelectorConfig


class WebEngineMessageSender(MessageSender):
    """Fill a visible chat input and click a visible send button inside WebEngine."""

    def __init__(self, page: QWebEnginePage, config: DomSelectorConfig | None = None) -> None:
        super().__init__()
        self.page = page
        self.config = config or DomSelectorConfig.load()
        self._pending_text = ""

    def send(self, text: str) -> bool:
        self._pending_text = text
        script = f"""
        (() => {{
          const text = {json.dumps(text, ensure_ascii=False)};
          const inputSelectors = {json.dumps(self.config.chat_input_selectors, ensure_ascii=False)};
          const buttonSelectors = {json.dumps(self.config.send_button_selectors, ensure_ascii=False)};
          const findFirst = (selectors) => {{
            for (const selector of selectors) {{
              const node = document.querySelector(selector);
              if (node) return node;
            }}
            return null;
          }};
          const input = findFirst(inputSelectors);
          if (!input) return JSON.stringify({{ ok: false, reason: "未找到弹幕输入框" }});
          input.focus();
          if (input.isContentEditable) {{
            input.innerText = text;
          }} else {{
            input.value = text;
          }}
          input.dispatchEvent(new InputEvent("input", {{ bubbles: true, inputType: "insertText", data: text }}));
          input.dispatchEvent(new Event("change", {{ bubbles: true }}));
          const button = findFirst(buttonSelectors);
          if (!button) return JSON.stringify({{ ok: false, reason: "未找到发送按钮" }});
          button.click();
          return JSON.stringify({{ ok: true }});
        }})();
        """
        self.page.runJavaScript(script, self._handle_result)
        return True

    def _handle_result(self, result: object) -> None:
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError:
                pass
        if isinstance(result, dict) and result.get("ok"):
            self.sent.emit(self._pending_text)
            return
        reason = "发送失败"
        if isinstance(result, dict) and result.get("reason"):
            reason = str(result["reason"])
        self.failed.emit(reason)
