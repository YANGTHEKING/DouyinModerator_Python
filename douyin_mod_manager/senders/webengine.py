from __future__ import annotations

import json

from PySide6.QtCore import Signal
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

    def refresh_sendability(self) -> None:
        self.page.runJavaScript(self._status_script(), self._handle_status_result)

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

    def _handle_status_result(self, result: object) -> None:
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError:
                result = None
        if not isinstance(result, dict):
            self.can_send = False
            self.status_reason = "状态检测失败"
            self.sendability_changed.emit(self.can_send, self.status_reason)
            return
        self.can_send = bool(result.get("canSend"))
        self.status_reason = str(result.get("reason") or ("可发送" if self.can_send else "不可发送"))
        self.sendability_changed.emit(self.can_send, self.status_reason)

    def _status_script(self) -> str:
        return f"(() => JSON.stringify(({self._status_script_body()})()))();"

    def _status_script_body(self) -> str:
        return f"""
        () => {{
          const inputSelectors = {json.dumps(self.config.chat_input_selectors, ensure_ascii=False)};
          const buttonSelectors = {json.dumps(self.config.send_button_selectors, ensure_ascii=False)};
          const loginSelectors = {json.dumps(self.config.login_indicator_selectors, ensure_ascii=False)};
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
          const textOf = (node) => String(node?.innerText || node?.textContent || node?.value || node?.placeholder || "").trim();
          const loginTextPattern = /需先登[录陆]才能开始聊天|登[录陆]后.*(?:弹幕|聊天)|先登[录陆].*(?:弹幕|聊天)|未登[录陆]/i;
          const findFirst = (selectors, predicate = () => true) => {{
            for (const selector of selectors) {{
              for (const node of document.querySelectorAll(selector)) {{
                if (predicate(node)) return node;
              }}
            }}
            return null;
          }};
          const explicitLoginPrompt = Array.from(document.querySelectorAll("div, span, p, button, textarea, input, [contenteditable='true']"))
            .map((node) => [node, textOf(node)])
            .filter(([node, text]) => isVisible(node) && loginTextPattern.test(text))
            .sort((a, b) => a[1].length - b[1].length)[0];
          if (explicitLoginPrompt) {{
            const matchedText = explicitLoginPrompt[1].match(loginTextPattern)?.[0] || explicitLoginPrompt[1];
            return {{ canSend: false, reason: `检测到未登录聊天框：${{matchedText.slice(0, 24)}}` }};
          }}
          const loginIndicator = findFirst(loginSelectors, (node) => {{
            if (!isVisible(node)) return false;
            const text = textOf(node);
            return /验证码|扫码|手机号|未登录|未登陆|login|sign in/i.test(text);
          }});
          if (loginIndicator) {{
            return {{ canSend: false, reason: `检测到登录提示：${{textOf(loginIndicator).slice(0, 40)}}` }};
          }}
          const input = findFirst(inputSelectors, (node) => isVisible(node) && !isDisabled(node));
          if (!input) return {{ canSend: false, reason: "未找到可用弹幕输入框，可能未登录或页面未加载完成" }};
          const inputText = textOf(input);
          if (/登录|登陆|先登录|未登录|未登陆|login|sign in/i.test(inputText)) {{
            return {{ canSend: false, reason: `输入框提示需要登录：${{inputText.slice(0, 40)}}` }};
          }}
          const button = findFirst(buttonSelectors, (node) => isVisible(node) && !isDisabled(node));
          if (!button) return {{ canSend: false, reason: "未找到可用发送按钮，可能未登录或发送受限" }};
          const buttonText = textOf(button);
          if (/登录|登陆|login|sign in/i.test(buttonText)) {{
            return {{ canSend: false, reason: `发送按钮是登录入口：${{buttonText.slice(0, 40)}}` }};
          }}
          return {{ canSend: true, reason: "已检测到可用输入框和发送按钮" }};
        }}
        """
