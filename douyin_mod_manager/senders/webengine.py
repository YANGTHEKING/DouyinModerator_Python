from __future__ import annotations

import json
import os

from PySide6.QtCore import QPoint, QTimer, Qt, Signal
from PySide6.QtNetwork import QNetworkCookie
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget

from douyin_mod_manager.senders.base import MessageSender
from douyin_mod_manager.sources.dom_config import DomSelectorConfig


class WebEngineMessageSender(MessageSender):
    """Fill a visible chat input and click a visible send button inside WebEngine."""

    sendability_changed = Signal(bool, str)

    def __init__(
        self,
        page: QWebEnginePage,
        config: DomSelectorConfig | None = None,
        view: QWidget | None = None,
    ) -> None:
        super().__init__()
        self.page = page
        self.config = config or DomSelectorConfig.load()
        self.view = view
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
        self._monitor(f"cookie gate has_session={has_session} cookie_count={len(self._cookies)}")
        self.page.runJavaScript(f"({self._status_script_body()})()", self._handle_sendability_result)

    def _handle_sendability_result(self, result: object) -> None:
        raw_result = result
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError:
                pass
        if not isinstance(result, dict):
            self._update_status(False, "页面发送状态检测失败")
            return
        self._monitor(
            "sendability "
            f"canSend={bool(result.get('canSend'))} "
            f"reason={result.get('reason')!r} "
            f"raw={raw_result!r}"
        )
        self._update_status(bool(result.get("canSend")), str(result.get("reason") or "页面发送状态未知"))

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
        self._monitor(f"send requested text={text!r}")
        if self.view is not None:
            return self._send_with_widget_events(text)
        script = f"""
        (() => {{
          const text = {json.dumps(text, ensure_ascii=False)};
          const inputSelectors = {json.dumps(self.config.chat_input_selectors, ensure_ascii=False)};
          const buttonSelectors = {json.dumps(self.config.send_button_selectors, ensure_ascii=False)};
          {self._dom_helper_script()}
          if (!input) return JSON.stringify({{ ok: false, reason: "未找到弹幕输入框" }});
          fillInput(input, text);
          setTimeout(() => {{
            const button = findSendButton(buttonSelectors);
            if (button) {{
              button.click();
              return;
            }}
            input.dispatchEvent(new KeyboardEvent("keydown", {{ key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true }}));
            input.dispatchEvent(new KeyboardEvent("keyup", {{ key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true }}));
          }}, 150);
          return JSON.stringify({{ ok: true }});
        }})();
        """
        self.page.runJavaScript(script, self._handle_result)
        return True

    def _send_with_widget_events(self, text: str) -> bool:
        script = f"""
        (() => {{
          const inputSelectors = {json.dumps(self.config.chat_input_selectors, ensure_ascii=False)};
          {self._dom_helper_script()}
          if (!input) return JSON.stringify({{ ok: false, reason: "未找到弹幕输入框" }});
          input.focus();
          const rect = input.getBoundingClientRect();
          return JSON.stringify({{
            ok: true,
            x: rect.x,
            y: rect.y,
            width: rect.width,
            height: rect.height,
            viewportWidth: window.innerWidth,
            viewportHeight: window.innerHeight,
            input: describeNode(input),
          }});
        }})();
        """
        self.page.runJavaScript(script, lambda result: self._handle_focus_result(result, text))
        return True

    def _handle_focus_result(self, result: object, text: str) -> None:
        raw_result = result
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError:
                pass
        self._monitor(f"focus result raw={raw_result!r} parsed={result!r}")
        if not isinstance(result, dict) or not result.get("ok"):
            reason = "未找到弹幕输入框"
            if isinstance(result, dict) and result.get("reason"):
                reason = str(result["reason"])
            self.failed.emit(reason)
            self.refresh_sendability()
            return
        view = self.view
        if view is None:
            self.failed.emit("WebEngine 视图不可用")
            return
        x, y = self._view_point_from_css_rect(result, view)
        view.setFocus()
        QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x, y))
        QTimer.singleShot(80, lambda: self._fill_and_submit_via_js(text))

    def _fill_and_submit_via_js(self, text: str) -> None:
        escaped = json.dumps(text, ensure_ascii=False)
        input_selectors = json.dumps(self.config.chat_input_selectors, ensure_ascii=False)
        button_selectors = json.dumps(self.config.send_button_selectors, ensure_ascii=False)
        inject = self._dom_helper_script()
        script = (
            "try {\n"
            f"var inputSelectors = {input_selectors};\n"
            f"var buttonSelectors = {button_selectors};\n"
            f"{inject}\n"
            f"var __dmmInput = input;\n"
            f"if (!__dmmInput) {{ window.__dmmSendResult = {{ ok: false, reason: '点击后未找到输入框' }}; }}\n"
            f"else {{\n"
            f"  __dmmInput.focus();\n"
            f"  fillInput(__dmmInput, {escaped});\n"
            f"  var __dmmBtn = findSendButton(buttonSelectors);\n"
            f"  if (__dmmBtn) {{ setTimeout(function() {{ __dmmBtn.click(); }}, 100); window.__dmmSendResult = {{ ok: true, method: 'button' }}; }}\n"
            f"  else {{\n"
            f"    __dmmInput.dispatchEvent(new KeyboardEvent('keydown', {{ key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true }}));\n"
            f"    __dmmInput.dispatchEvent(new KeyboardEvent('keyup', {{ key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true }}));\n"
            f"    window.__dmmSendResult = {{ ok: true, method: 'enter' }};\n"
            f"  }}\n"
            f"}}\n"
            "} catch(e) { window.__dmmSendResult = { ok: false, reason: 'JS异常: ' + e.message }; }"
        )
        self.page.runJavaScript(script, lambda _: QTimer.singleShot(200, self._read_send_result))

    def _read_send_result(self) -> None:
        self.page.runJavaScript(
            "JSON.stringify(window.__dmmSendResult || {ok:false,reason:'无结果'})",
            self._handle_js_submit_result,
        )

    def _handle_js_submit_result(self, result: object) -> None:
        raw = result
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError:
                pass
        self._monitor(f"js submit result raw={raw!r} parsed={result!r}")
        if isinstance(result, dict) and result.get("ok"):
            self.sent.emit(self._pending_text)
        else:
            reason = "JS发送失败"
            if isinstance(result, dict) and result.get("reason"):
                reason = str(result["reason"])
            self.failed.emit(reason)
            self.refresh_sendability()

    @staticmethod
    def _view_point_from_css_rect(result: dict, view: QWidget) -> tuple[int, int]:
        viewport_width = max(float(result.get("viewportWidth") or view.width() or 1), 1.0)
        viewport_height = max(float(result.get("viewportHeight") or view.height() or 1), 1.0)
        css_x = float(result.get("x") or 0) + min(float(result.get("width") or 1) / 2, 24)
        css_y = float(result.get("y") or 0) + min(float(result.get("height") or 1) / 2, 16)
        x = int(css_x * view.width() / viewport_width)
        y = int(css_y * view.height() / viewport_height)
        return max(1, min(view.width() - 2, x)), max(1, min(view.height() - 2, y))

    def _handle_result(self, result: object) -> None:
        raw_result = result
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError:
                pass
        self._monitor(f"send result raw={raw_result!r} parsed={result!r}")
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
          {self._dom_helper_script()}
          if (!input) return JSON.stringify({{ canSend: false, reason: "未找到可用弹幕输入框" }});
          const button = findSendButton(buttonSelectors);
          return JSON.stringify({{
            canSend: true,
            reason: button ? "已检测到可用输入框和发送按钮" : "已检测到可用输入框，将使用回车发送",
            input: describeNode(input),
            button: button ? describeNode(button) : null,
          }});
        }}
        """

    def _dom_helper_script(self) -> str:
        return """
          const unique = (nodes) => Array.from(new Set(nodes.filter(Boolean)));
          const isVisible = (node) => {
            if (!node) return false;
            const style = window.getComputedStyle(node);
            const rect = node.getBoundingClientRect();
            return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
          };
          const isDisabled = (node) => {
            if (!node) return true;
            return Boolean(node.disabled || node.readOnly || node.getAttribute("aria-disabled") === "true" || node.classList.contains("disabled"));
          };
          const hasEditableAttr = (node) => {
            const attr = (node.getAttribute("contenteditable") || "").toLowerCase();
            return node.isContentEditable || (attr !== "" && attr !== "false");
          };
          const collect = (selectors) => {
            const nodes = [];
            for (const selector of selectors) {
              try {
                nodes.push(...document.querySelectorAll(selector));
              } catch (_) {}
            }
            return unique(nodes);
          };
          const textOf = (node) => [
            node.getAttribute("aria-label"),
            node.getAttribute("placeholder"),
            node.getAttribute("data-e2e"),
            node.className,
            node.textContent,
          ].filter(Boolean).join(" ");
          const describeNode = (node) => {
            if (!node) return null;
            const rect = node.getBoundingClientRect();
            return {
              tag: node.tagName,
              role: node.getAttribute("role") || "",
              dataE2e: node.getAttribute("data-e2e") || "",
              ariaLabel: node.getAttribute("aria-label") || "",
              placeholder: node.getAttribute("placeholder") || "",
              className: String(node.className || "").slice(0, 120),
              text: String(node.textContent || node.value || "").slice(0, 120),
              rect: { x: Math.round(rect.x), y: Math.round(rect.y), width: Math.round(rect.width), height: Math.round(rect.height) },
            };
          };
          const scoreInput = (node) => {
            if (!isVisible(node) || isDisabled(node)) return -1;
            const tag = node.tagName.toLowerCase();
            const type = (node.getAttribute("type") || "text").toLowerCase();
            const editable = hasEditableAttr(node);
            const role = node.getAttribute("role") || "";
            if (role === "combobox") return -1;
            const roleTextbox = role === "textbox";
            const formInput = tag === "textarea" || (tag === "input" && ["", "text", "search"].includes(type));
            if (!editable && !roleTextbox && !formInput) return -1;
            const haystack = textOf(node);
            if (/搜索|国家|地区|手机|验证码|密码|登录|search|country|region|phone|password|captcha|login/i.test(haystack)) return -1;
            const chatIntent = /输入|评论|弹幕|发言|留言|comment|chat|danmaku|message/i.test(haystack);
            if (formInput && !node.matches("[data-dmm-chat-input]") && !chatIntent) return -1;
            let score = formInput ? 40 : 25;
            if (node.matches("[data-dmm-chat-input]")) score += 100;
            if (roleTextbox) score += 35;
            if (editable) score += 30;
            if (chatIntent) score += 30;
            if (node.closest(".webcast-chatroom___item,.chat-item,.comment-item,.message-item")) score -= 80;
            return score;
          };
          const findInput = (selectors) => collect(selectors)
            .map((node) => ({ node, score: scoreInput(node) }))
            .filter((entry) => entry.score >= 0)
            .sort((a, b) => b.score - a.score)[0]?.node || null;
          const scoreButton = (node) => {
            if (!isVisible(node) || isDisabled(node)) return -1;
            const roleButton = node.getAttribute("role") === "button";
            const tag = node.tagName.toLowerCase();
            const explicitSendSelector = node.matches("[data-dmm-send],[data-e2e*='send'],.send-button,.send,button[type='submit']");
            if (tag !== "button" && !roleButton && !explicitSendSelector) return -1;
            const rect = node.getBoundingClientRect();
            if (rect.width < 12 || rect.height < 12) return -1;
            const inputRect = input ? input.getBoundingClientRect() : null;
            const centerX = rect.left + rect.width / 2;
            const centerY = rect.top + rect.height / 2;
            const nearInputRight = inputRect
              && centerX >= inputRect.right - 24
              && centerX <= inputRect.right + 180
              && centerY >= inputRect.top - 32
              && centerY <= inputRect.bottom + 48;
            const nearInputBelow = inputRect
              && centerX >= inputRect.left
              && centerX <= inputRect.right + 120
              && centerY >= inputRect.bottom - 12
              && centerY <= inputRect.bottom + 72;
            let score = tag === "button" || roleButton ? 30 : 15;
            const haystack = textOf(node);
            if (/读屏|无障碍|accessibility|screen\\s*reader/i.test(haystack)) return -1;
            const sendIntent = /发送|send|评论|弹幕|发出/i.test(haystack);
            if (!explicitSendSelector && !sendIntent && !nearInputRight && !nearInputBelow) return -1;
            if (node.matches("[data-dmm-send]")) score += 100;
            if (sendIntent) score += 45;
            if (nearInputRight) score += 35;
            if (nearInputBelow) score += 20;
            if (!sendIntent && !explicitSendSelector && !nearInputRight) score -= 20;
            return score;
          };
          const findSendButton = (selectors) => {
            const candidates = [
              ...collect(selectors),
              ...document.querySelectorAll("button,[role='button'],[data-e2e*='send'],.send-button,.send"),
            ];
            return unique(candidates)
              .map((node) => ({ node, score: scoreButton(node) }))
              .filter((entry) => entry.score >= 0)
              .sort((a, b) => b.score - a.score)[0]?.node || null;
          };
          const dispatchInput = (node, value) => {
            try {
              node.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: value }));
            } catch (_) {
              node.dispatchEvent(new Event("input", { bubbles: true }));
            }
            node.dispatchEvent(new Event("change", { bubbles: true }));
            node.dispatchEvent(new KeyboardEvent("keyup", { key: value.slice(-1), bubbles: true }));
          };
          const fillInput = (input, value) => {
            input.focus();
            if (hasEditableAttr(input)) {
              const range = document.createRange();
              range.selectNodeContents(input);
              const selection = window.getSelection();
              selection.removeAllRanges();
              selection.addRange(range);
              document.execCommand("delete", false);
              document.execCommand("insertText", false, value);
              if ((input.textContent || "").trim() !== value.trim()) {
                input.textContent = value;
              }
              dispatchInput(input, value);
              return;
            }
            const prototype = input.tagName.toLowerCase() === "textarea" ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
            const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
            if (setter) setter.call(input, value);
            else input.value = value;
            dispatchInput(input, value);
          };
          const input = findInput(inputSelectors);
        """

    def _monitor(self, message: str) -> None:
        if os.environ.get("DMM_MONITOR_SEND") == "1":
            print(f"[DMM-MONITOR] {message}", flush=True)
