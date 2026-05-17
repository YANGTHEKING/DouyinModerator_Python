from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWebEngineCore import QWebEnginePage

from douyin_mod_manager.core.events import EventType, LiveEvent
from douyin_mod_manager.sources.base import EventSource
from douyin_mod_manager.sources.dom_config import DomSelectorConfig
from douyin_mod_manager.sources.dom_parser import normalize_dom_record


class WebEngineDomEventSource(EventSource):
    """Read-only DOM observer for visible live-room events inside Qt WebEngine."""

    parse_debug_received = Signal(object)

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
        self.audit_path = Path(__file__).resolve().parents[2] / "data" / "dom_parse_audit.jsonl"

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

          const nodeText = (node) => String(node?.innerText || node?.textContent || node?.value || "").trim();

          const isVisible = (node) => {{
            if (!node) return false;
            const style = window.getComputedStyle(node);
            const rect = node.getBoundingClientRect();
            return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
          }};

          const isInteractive = (node) => {{
            const style = window.getComputedStyle(node);
            const role = String(node.getAttribute?.("role") || "").toLowerCase();
            return style.cursor === "pointer"
              || node.tabIndex >= 0
              || Boolean(node.onclick)
              || role === "button"
              || role === "link"
              || node.tagName === "A"
              || node.tagName === "BUTTON";
          }};

          const splitAfterUsername = (text, username) => {{
            if (!text || !username) return "";
            const lines = text.split(/\\n+/).map((line) => line.trim()).filter(Boolean);
            const usernameLine = [...lines].reverse().find((line) => line.includes(username)) || text;
            const at = usernameLine.indexOf(username);
            if (at < 0) return "";
            return usernameLine.slice(at + username.length).replace(/^\\s*[：:]\\s*/, "").trim();
          }};

          const structuredParts = (item, text) => {{
            const selectorUsername = firstText(item, config.usernameSelectors);
            if (selectorUsername) {{
              return {{
                username: selectorUsername,
                content: firstText(item, config.contentSelectors) || splitAfterUsername(text, selectorUsername),
                method: "selector"
              }};
            }}

            const candidates = Array.from(item.querySelectorAll("*"))
              .filter((node) => node !== item && isVisible(node))
              .map((node) => {{
                const value = nodeText(node);
                const className = String(node.className || "");
                const lineWithColon = text.split(/\\n+/).find((line) => line.includes(value) && /[：:]/.test(line)) || "";
                let score = 0;
                if (!value || value.length > 80) score -= 100;
                if (isInteractive(node)) score += 30;
                if (/user|nick|name|author|avatar/i.test(className)) score += 20;
                if (lineWithColon.startsWith(value)) score += 80;
                else if (lineWithColon.includes(`${{value}}：`) || lineWithColon.includes(`${{value}}:`)) score += 60;
                if (/^(九宝|粉丝团|灯牌|Lv\\.?\\d+|\\d+)$/i.test(value)) score -= 50;
                return {{ node, value, score }};
              }})
              .filter((candidate) => candidate.score > 0)
              .sort((a, b) => b.score - a.score);
            const best = candidates[0];
            if (!best) return {{ username: "", content: "", method: "text" }};
            const content = splitAfterUsername(text, best.value);
            const username = content ? best.value.replace(/[：:]$/, "").trim() : best.value;
            return {{
              username,
              content,
              method: "interactive",
              usernameClassName: String(best.node.className || ""),
              usernameTagName: best.node.tagName || ""
            }};
          }};

          const mediaLabels = (root) => {{
            const labels = [];
            root.querySelectorAll?.("img, svg, [aria-label], [title], [alt]").forEach((node) => {{
              for (const attr of ["alt", "title", "aria-label"]) {{
                const value = node.getAttribute?.(attr);
                if (value && String(value).trim()) labels.push(String(value).trim());
              }}
            }});
            return [...new Set(labels)];
          }};

          const giftCountFromText = (text) => {{
            const match = String(text || "").match(/[xX×*＊]\\s*(\\d+)|(\\d+)\\s*(?:个|份|枚)/);
            if (!match) return 1;
            return Number(match[1] || match[2] || 1) || 1;
          }};

          const giftNameFromHintText = (text) => {{
            let value = String(text || "").replace(/\\s+/g, " ").trim();
            if (!/(送出了|送出|赠送|送了|送给|送上)/.test(value)) return "";
            value = value.replace(/^.*?(送出了|送出|赠送|送了|送给|送上)/, "").trim();
            value = value.replace(/[xX×*＊]\\s*\\d+.*$/, "").trim();
            value = value.replace(/^主播|^你|^TA|^ta|^Ta/, "").trim();
            value = value.replace(/^[：:，,\\s]+|[：:，,\\s]+$/g, "");
            if (!value || /^礼物$/.test(value) || /^\\d+$/.test(value)) return "";
            if (value.length > 30) return "";
            return value;
          }};

          const giftUsernameFromHintText = (text) => {{
            const value = String(text || "").replace(/\\s+/g, " ").trim();
            const match = value.match(/^(.+?)(?:送出了|送出|赠送|送了|送给|送上)/);
            if (!match) return "";
            const username = match[1].replace(/[：:，,\\s]+$/g, "").trim();
            if (!username || username.length > 80) return "";
            return username;
          }};

          const rememberGiftHint = (node) => {{
            const contexts = [node, node.parentElement, node.parentElement?.parentElement].filter(Boolean);
            let best = null;
            for (const context of contexts) {{
              const text = nodeText(context);
              if (!text || text.length > 240 || !/(送出了|送出|赠送|送了|送给|送上)/.test(text)) continue;
              const giftName = giftNameFromHintText(text);
              if (!giftName) continue;
              const username = giftUsernameFromHintText(text);
              const score = (username ? 10 : 0) - Math.max(0, text.length - 80) / 80;
              if (!best || score > best.score) best = {{ context, text, giftName, username, score }};
            }}
            if (!best) return;
            window.__dmmGiftHints = window.__dmmGiftHints || [];
            window.__dmmGiftHints.push({{
              at: Date.now(),
              text: best.text,
              username: best.username,
              giftName: best.giftName,
              giftCount: giftCountFromText(best.text),
              mediaLabels: mediaLabels(best.context)
            }});
            window.__dmmGiftHints = window.__dmmGiftHints.slice(-40);
          }};

          const recentGiftHints = () => {{
            const now = Date.now();
            window.__dmmGiftHints = (window.__dmmGiftHints || []).filter((hint) => now - hint.at < 8000);
            return window.__dmmGiftHints.slice(-8);
          }};

          const childSummaries = (root) => Array.from(root.querySelectorAll?.("*") || [])
            .filter((node) => isVisible(node))
            .slice(0, 18)
            .map((node) => {{
              const style = window.getComputedStyle(node);
              return {{
                tag: node.tagName || "",
                className: String(node.className || "").slice(0, 120),
                text: nodeText(node).slice(0, 80),
                alt: String(node.getAttribute?.("alt") || "").slice(0, 80),
                title: String(node.getAttribute?.("title") || "").slice(0, 80),
                ariaLabel: String(node.getAttribute?.("aria-label") || "").slice(0, 80),
                src: String(node.getAttribute?.("src") || "").slice(0, 160),
                color: style.color || "",
                backgroundColor: style.backgroundColor || "",
                backgroundImage: String(style.backgroundImage || "").slice(0, 160)
              }};
            }});

          const inferType = (node, text, content) => {{
            const explicit = node.dataset?.dmmType || node.getAttribute?.("data-type") || "";
            if (explicit) return explicit;
            if (/^(送出了|送出|赠送|送了|送给|送上)/.test(content || "")) return "gift";
            if (!/[：:]/.test(text) && /(送出|赠送|送了|送给|送上|gift)/i.test(text)) return "gift";
            if (!/[：:]/.test(text) && /\\s+(关注了主播|关注主播|加入了粉丝团|成为了粉丝)\\s*$/.test(text)) return "follow";
            if (!/[：:]/.test(text) && /\\s+(来了|进入直播间|进入了直播间|进场)\\s*$/.test(text)) return "user_enter";
            if (/为主播点赞了|推荐直播给Ta的朋友|推荐了直播|刚刚升级至Lv\\./.test(text)) return "system";
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
              const parts = structuredParts(item, text);
              const username = parts.username;
              const content = parts.content || text;
              const type = inferType(item, text, content);
              const labels = mediaLabels(item);
              const style = window.getComputedStyle(item);
              const children = childSummaries(item);
              window.__dmmEvents.push({{
                type,
                username,
                content,
                raw: {{
                  text,
                  mediaLabels: labels,
                  parseMethod: parts.method,
                  usernameClassName: parts.usernameClassName || "",
                  usernameTagName: parts.usernameTagName || "",
                  color: style.color || "",
                  backgroundColor: style.backgroundColor || "",
                  childSummaries: children,
                  giftHints: type === "gift" ? recentGiftHints() : [],
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
              mutation.addedNodes.forEach((node) => {{
                if (node?.nodeType === Node.ELEMENT_NODE) rememberGiftHint(node);
                parseNode(node);
              }});
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
            parsed = normalize_dom_record(record)
            if parsed is None:
                self._publish_parse_debug(record, None)
                continue
            key = f"{parsed.type}|{parsed.username}|{parsed.content}"
            if key in self._seen_keys:
                continue
            self._seen_keys.add(key)
            self._publish_parse_debug(record, parsed)
            event = LiveEvent(
                type=self._event_type(parsed.type),
                session_id=self.session_id,
                username=parsed.username,
                content=parsed.content,
                raw=parsed.raw,
                source="webengine_dom",
            )
            self.event_received.emit(event)

    @staticmethod
    def _event_type(value: object) -> EventType:
        try:
            return EventType(str(value))
        except ValueError:
            return EventType.CHAT

    def _publish_parse_debug(self, record: dict, parsed: object | None) -> None:
        payload = self._build_audit_payload(record, parsed)
        self.parse_debug_received.emit(payload)
        self._write_audit(payload)

    def _build_audit_payload(self, record: dict, parsed: object | None) -> dict:
        raw = record.get("raw") if isinstance(record.get("raw"), dict) else {}
        parsed_payload = None
        if parsed is not None:
            parsed_payload = {
                "type": getattr(parsed, "type", None),
                "username": getattr(parsed, "username", None),
                "content": getattr(parsed, "content", None),
                "gift_name": getattr(parsed, "raw", {}).get("gift_name"),
                "gift_count": getattr(parsed, "raw", {}).get("gift_count"),
                "gift_image_url": getattr(parsed, "raw", {}).get("gift_image_url"),
            }
        payload = {
            "at": datetime.now(timezone.utc).isoformat(),
            "source": "webengine_dom",
            "raw": {
                "type": record.get("type"),
                "username": record.get("username"),
                "content": record.get("content"),
                "text": raw.get("text"),
                "mediaLabels": raw.get("mediaLabels"),
                "parseMethod": raw.get("parseMethod"),
                "usernameClassName": raw.get("usernameClassName"),
                "usernameTagName": raw.get("usernameTagName"),
                "color": raw.get("color"),
                "backgroundColor": raw.get("backgroundColor"),
                "childSummaries": raw.get("childSummaries"),
                "giftHints": raw.get("giftHints"),
                "className": raw.get("className"),
                "tagName": raw.get("tagName"),
                "url": raw.get("url"),
            },
            "parsed": parsed_payload,
        }
        return payload

    def _write_audit(self, payload: dict) -> None:
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
