from __future__ import annotations

import json

from PySide6.QtCore import QTimer, Signal
from PySide6.QtNetwork import QNetworkCookie
from PySide6.QtWebEngineCore import QWebEnginePage

from douyin_mod_manager.senders.base import MessageSender
from douyin_mod_manager.sources.dom_config import DomSelectorConfig


class WebEngineMessageSender(MessageSender):
    """Fill a visible chat input and click a visible send button inside WebEngine."""

    sendability_changed = Signal(bool, str)

    def __init__(self, page: QWebEnginePage, config: DomSelectorConfig | None = None) -> None:
        super().__init__()
        self.page = page
        self.config = config or DomSelectorConfig.load()
        self._pending_text = ""
        self.can_send = False
        self.status_reason = "尚未检测"
        self._cookies: dict[str, str] = {}

        cookie_store = self.page.profile().cookieStore()
        cookie_store.cookieAdded.connect(self._on_cookie_added)
        cookie_store.cookieRemoved.connect(self._on_cookie_removed)

    def _on_cookie_added(self, cookie: QNetworkCookie) -> None:
        name = bytes(cookie.name()).decode("utf-8", errors="replace")
        value = bytes(cookie.value()).decode("utf-8", errors="replace")
        self._cookies[name] = value

    def _on_cookie_removed(self, cookie: QNetworkCookie) -> None:
        name = bytes(cookie.name()).decode("utf-8", errors="replace")
        self._cookies.pop(name, None)

    def refresh_sendability(self) -> None:
        cookie_store = self.page.profile().cookieStore()
        cookie_store.loadAllCookies()
        QTimer.singleShot(600, self._check_cookies)

    def _check_cookies(self) -> None:
        has_session = (
            (self._cookies.get("sessionid") or "").strip() != ""
            or (self._cookies.get("sessionid_ss") or "").strip() != ""
        )
        if has_session:
            self._update_status(True, "已检测到登录Cookie（sessionid）")
        else:
            self._update_status(False, "未检测到登录Cookie，请先登录")

    def _update_status(self, can_send: bool, reason: str) -> None:
        changed = self.can_send != can_send or self.status_reason != reason
        self.can_send = can_send
        self.status_reason = reason
        if changed:
            self.sendability_changed.emit(can_send, reason)

    def send(self, text: str) -> bool:
        if not self.can_send:
            self.failed.emit(f"当前不可发送：{self.status_reason}")
            return False
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
          const status = ({self._status_script_body()})();
          if (!status.canSend) return JSON.stringify({{ ok: false, reason: status.reason }});
          const input = findFirst(inputSelectors);
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
        self.refresh_sendability()

    def _status_script_body(self) -> str:
        return f"""
        () => {{
          const inputSelectors = {json.dumps(self.config.chat_input_selectors, ensure_ascii=False)};
          const buttonSelectors = {json.dumps(self.config.send_button_selectors, ensure_ascii=False)};
          const isVisible = (node) => {{
            if (!node) return false;
            const style = window.getComputedStyle(node);
            const rect = node.getBoundingClientRect();
            return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
          }};
          const isDisabled = (node) => {{
            if (!node) return true;
            return Boolean(node.disabled || node.getAttribute("aria-disabled") === "true" || node.classList.contains("disabled"));
          }};
          const findFirst = (selectors, predicate = () => true) => {{
            for (const selector of selectors) {{
              for (const node of document.querySelectorAll(selector)) {{
                if (predicate(node)) return node;
              }}
            }}
            return null;
          }};
          const input = findFirst(inputSelectors, (node) => isVisible(node) && !isDisabled(node));
          if (!input) return {{ canSend: false, reason: "未找到可用弹幕输入框" }};
          const button = findFirst(buttonSelectors, (node) => isVisible(node) && !isDisabled(node));
          if (!button) return {{ canSend: false, reason: "未找到可用发送按钮" }};
          return {{ canSend: true, reason: "已检测到可用输入框和发送按钮" }};
        }}
        """
