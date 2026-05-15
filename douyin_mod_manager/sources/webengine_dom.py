from __future__ import annotations

import json

from PySide6.QtCore import QTimer
from PySide6.QtWebEngineCore import QWebEnginePage

from douyin_mod_manager.core.events import EventType, LiveEvent
from douyin_mod_manager.sources.base import EventSource
from douyin_mod_manager.sources.dom_config import DomSelectorConfig


class WebEngineDomEventSource(EventSource):
    """Read-only DOM observer for visible live-room events inside Qt WebEngine."""

    def __init__(self, page: QWebEnginePage, session_id: str, config: DomSelectorConfig | None = None) -> None:
        super().__init__()
        self.page = page
        self.session_id = session_id
        self.config = config or DomSelectorConfig.load()
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(800)
        self.poll_timer.timeout.connect(self._poll_events)
        self._seen_keys: set[str] = set()
        self._enabled = False

    def start(self) -> None:
        self._enabled = True
        self._install_observer()
        self.poll_timer.start()
        self.status_changed.emit("WebEngine DOM 观察器已启动")

    def stop(self) -> None:
        self._enabled = False
        self.poll_timer.stop()
        self.status_changed.emit("WebEngine DOM 观察器已停止")

    def reinstall(self) -> None:
        if self._enabled:
            self._install_observer()

    def _install_observer(self) -> None:
        script = f"""
        (() => {{
          const config = {self.config.to_javascript_object()};
          window.__dmmEvents = window.__dmmEvents || [];
          window.__dmmSeen = window.__dmmSeen || new Set();
          if (window.__dmmObserver) {{
            window.__dmmObserver.disconnect();
          }}

          const firstText = (root, selectors) => {{
            for (const selector of selectors) {{
              const found = root.matches?.(selector) ? root : root.querySelector?.(selector);
              const text = found?.innerText || found?.textContent || found?.value;
              if (text && String(text).trim()) return String(text).trim();
            }}
            return "";
          }};

          const inferType = (node, text) => {{
            const explicit = node.dataset?.dmmType || node.getAttribute?.("data-type") || "";
            if (explicit) return explicit;
            if (/送出|礼物|gift/i.test(text)) return "gift";
            if (/关注/.test(text)) return "follow";
            if (/进入|来了|进场/.test(text)) return "user_enter";
            if (/系统|提示|直播间/.test(text)) return "system";
            return "chat";
          }};

          const parseNode = (node) => {{
            if (!node || node.nodeType !== Node.ELEMENT_NODE) return;
            const container = config.eventContainers.some((selector) => node.matches?.(selector));
            const descendants = container ? [node] : Array.from(node.querySelectorAll?.(config.eventContainers.join(",")) || []);
            for (const item of descendants) {{
              const text = (item.innerText || item.textContent || "").trim();
              if (!text) continue;
              const key = `${{location.href}}|${{text}}`;
              if (window.__dmmSeen.has(key)) continue;
              window.__dmmSeen.add(key);
              const username = firstText(item, config.usernameSelectors);
              const content = firstText(item, config.contentSelectors) || text.replace(username, "").trim() || text;
              const type = inferType(item, text);
              window.__dmmEvents.push({{
                type,
                username,
                content,
                raw: {{
                  text,
                  url: location.href,
                  className: item.className || "",
                  tagName: item.tagName || ""
                }}
              }});
            }}
          }};

          document.querySelectorAll(config.eventContainers.join(",")).forEach(parseNode);
          window.__dmmObserver = new MutationObserver((mutations) => {{
            for (const mutation of mutations) {{
              mutation.addedNodes.forEach(parseNode);
            }}
          }});
          window.__dmmObserver.observe(document.documentElement || document.body, {{
            childList: true,
            subtree: true
          }});
          return true;
        }})();
        """
        self.page.runJavaScript(script, lambda ok: self.status_changed.emit("DOM 观察脚本已注入" if ok else "DOM 观察脚本注入失败"))

    def _poll_events(self) -> None:
        if not self._enabled:
            return
        self.page.runJavaScript(
            "(() => { const events = window.__dmmEvents || []; window.__dmmEvents = []; return JSON.stringify(events); })();",
            self._handle_events,
        )

    def _handle_events(self, records: object) -> None:
        if isinstance(records, str):
            if not records:
                return
            try:
                records = json.loads(records)
            except json.JSONDecodeError:
                return
        if not isinstance(records, list):
            return
        for record in records:
            if not isinstance(record, dict):
                continue
            key = f"{record.get('type')}|{record.get('username')}|{record.get('content')}"
            if key in self._seen_keys:
                continue
            self._seen_keys.add(key)
            raw = record.get("raw") if isinstance(record.get("raw"), dict) else {}
            event = LiveEvent(
                type=self._event_type(record.get("type")),
                session_id=self.session_id,
                username=str(record.get("username") or "") or None,
                content=str(record.get("content") or "") or None,
                raw=raw,
                source="webengine_dom",
            )
            self.event_received.emit(event)

    @staticmethod
    def _event_type(value: object) -> EventType:
        try:
            return EventType(str(value))
        except ValueError:
            return EventType.CHAT
