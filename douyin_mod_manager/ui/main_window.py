from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QPoint, QUrl, Qt
from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from douyin_mod_manager.core.events import EVENT_LABELS, MODE_LABELS, ActionProposal, LiveEvent, LiveMode
from douyin_mod_manager.core.rate_limit import SlidingWindowLimiter
from douyin_mod_manager.core.rules import RuleEngine
from douyin_mod_manager.core.sessions import LiveSession
from douyin_mod_manager.features.gift_thanks import GiftThanksStrategy
from douyin_mod_manager.features.risk import RiskDetector
from douyin_mod_manager.features.song_queue import SongQueueService
from douyin_mod_manager.senders.mock import MockMessageSender
from douyin_mod_manager.senders.webengine import WebEngineMessageSender
from douyin_mod_manager.sources.mock import MockEventSource
from douyin_mod_manager.sources.webengine_dom import WebEngineDomEventSource
from douyin_mod_manager.storage.database import Database
from douyin_mod_manager.storage.repositories import SongRepository, SongStatus


class MainWindow(QMainWindow):
    def __init__(self, database: Database) -> None:
        super().__init__()
        self.database = database
        self.session = LiveSession(name=f"虚拟主播场次 {datetime.now().strftime('%H:%M')}")
        self.database.save_session(self.session)

        self.rule_engine = RuleEngine()
        self.song_repository = SongRepository(database)
        self.song_service = SongQueueService(self.song_repository)
        self.risk_detector = RiskDetector()
        self.gift_strategy = GiftThanksStrategy()
        self.limiter = SlidingWindowLimiter(max_events=3, window_seconds=60)
        self.auto_paused = False
        self._pending_action_by_text: dict[str, ActionProposal] = {}
        self.source = None

        self.setWindowTitle("抖音虚拟主播房管管理器 - 本地原型")
        self._build_ui()

        self.mock_source = MockEventSource(self.session.id)
        self.web_source = WebEngineDomEventSource(self.web_view.page(), self.session.id)
        self.mock_sender = MockMessageSender()
        self.web_sender = WebEngineMessageSender(self.web_view.page())
        self.sender = self.mock_sender
        self.login_status_timer = QTimer(self)
        self.login_status_timer.setInterval(5000)
        self.login_status_timer.timeout.connect(self.web_sender.refresh_sendability)
        self.auto_like_timer = QTimer(self)
        self.auto_like_timer.setInterval(15000)
        self.auto_like_timer.timeout.connect(self.perform_live_like)

        self._connect_signals()
        self._activate_mock_source()
        self.statusBar().showMessage("模拟源已启动，当前为 Mock 发送器")

    def _build_ui(self) -> None:
        toolbar = QToolBar("控制")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self.mode_combo = QComboBox()
        for mode, label in MODE_LABELS.items():
            self.mode_combo.addItem(label, mode.value)
        toolbar.addWidget(QLabel("直播模式 "))
        toolbar.addWidget(self.mode_combo)

        self.source_combo = QComboBox()
        self.source_combo.addItem("模拟源", "mock")
        self.source_combo.addItem("WebEngine", "webengine")
        toolbar.addWidget(QLabel(" 事件源 "))
        toolbar.addWidget(self.source_combo)

        self.pause_button = QPushButton("暂停自动发送")
        self.pause_button.setCheckable(True)
        toolbar.addWidget(self.pause_button)

        self.clear_button = QPushButton("清空日志")
        toolbar.addWidget(self.clear_button)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_web_placeholder())
        splitter.addWidget(self._build_event_tabs())
        splitter.addWidget(self._build_action_panel())
        splitter.setSizes([480, 520, 420])
        self.setCentralWidget(splitter)
        self.setStatusBar(QStatusBar())

    def _build_web_placeholder(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)

        title = QLabel("WebEngine 页面区")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        layout.addWidget(title)

        controls = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setText("919812874685")
        self.url_input.setPlaceholderText("输入抖音直播房间号")
        self.load_url_button = QPushButton("加载")
        self.load_demo_button = QPushButton("Demo")
        self.start_dom_button = QPushButton("启动观察")
        controls.addWidget(self.url_input, 1)
        controls.addWidget(self.load_url_button)
        controls.addWidget(self.load_demo_button)
        controls.addWidget(self.start_dom_button)
        layout.addLayout(controls)

        self.login_status_label = QLabel("登录/发送状态：自动检测中")
        self.login_status_label.setStyleSheet("color: #666;")
        layout.addWidget(self.login_status_label)

        self.web_view = QWebEngineView()
        self.web_view.setHtml(
            """
            <html><body style="font-family: -apple-system; padding: 24px;">
              <h2>WebEngine 页面区</h2>
              <p>加载本地 demo 或你手动登录后的直播间页面。</p>
              <p>DOM 观察器只读取页面上已经展示的内容。</p>
            </body></html>
            """
        )
        layout.addWidget(self.web_view, 1)
        return panel

    def _build_event_tabs(self) -> QWidget:
        tabs = QTabWidget()
        self.event_table = QTableWidget(0, 4)
        self.event_table.setHorizontalHeaderLabels(["时间", "类型", "用户", "内容"])
        self.event_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        tabs.addTab(self.event_table, "实时事件")

        self.song_table = QTableWidget(0, 4)
        self.song_table.setHorizontalHeaderLabels(["状态", "歌名", "点歌人", "备注"])
        self.song_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        tabs.addTab(self.song_table, "歌单")

        self.rule_list = QListWidget()
        for rule in self.rule_engine.rules:
            self.rule_list.addItem(f"{'开' if rule.enabled else '关'} | {rule.name} | {EVENT_LABELS[rule.event_type]}")
        tabs.addTab(self.rule_list, "规则")

        debug_panel = QWidget()
        debug_layout = QVBoxLayout(debug_panel)
        self.parse_debug_table = QTableWidget(0, 5)
        self.parse_debug_table.setHorizontalHeaderLabels(["时间", "原始类型", "原文", "媒体标签", "解析结果"])
        self.parse_debug_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.parse_debug_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        debug_layout.addWidget(self.parse_debug_table, 2)
        self.parse_debug_detail = QTextEdit()
        self.parse_debug_detail.setReadOnly(True)
        self.parse_debug_detail.setPlaceholderText("选择一条解析记录查看 raw / parsed JSON")
        debug_layout.addWidget(self.parse_debug_detail, 1)
        tabs.addTab(debug_panel, "解析调试")
        return tabs

    def _build_action_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)

        layout.addWidget(QLabel("待确认回复 / 自动动作"))
        self.action_list = QListWidget()
        layout.addWidget(self.action_list, 2)

        buttons = QHBoxLayout()
        self.send_selected_button = QPushButton("发送选中")
        self.discard_selected_button = QPushButton("丢弃选中")
        buttons.addWidget(self.send_selected_button)
        buttons.addWidget(self.discard_selected_button)
        layout.addLayout(buttons)

        layout.addWidget(QLabel("快捷回复"))
        self.quick_replies = QListWidget()
        for text in [
            "点歌请发送：点歌 歌名",
            "当前暂不接点歌，感谢理解。",
            "请大家文明交流，专注直播内容。",
            "PK 中理性应援，文明观看。",
        ]:
            self.quick_replies.addItem(text)
        layout.addWidget(self.quick_replies, 1)
        self.send_quick_button = QPushButton("发送快捷回复")
        layout.addWidget(self.send_quick_button)

        layout.addWidget(QLabel("直播间点赞"))
        like_buttons = QHBoxLayout()
        self.like_once_button = QPushButton("点赞一次")
        self.auto_like_button = QPushButton("自动低频点赞")
        self.auto_like_button.setCheckable(True)
        like_buttons.addWidget(self.like_once_button)
        like_buttons.addWidget(self.auto_like_button)
        layout.addLayout(like_buttons)
        self.like_status_label = QLabel("点赞状态：未启动")
        self.like_status_label.setStyleSheet("color: #666;")
        layout.addWidget(self.like_status_label)
        return panel

    def _connect_signals(self) -> None:
        self.mock_source.event_received.connect(self.on_event)
        self.mock_source.status_changed.connect(self.statusBar().showMessage)
        self.web_source.event_received.connect(self.on_event)
        self.web_source.status_changed.connect(self.statusBar().showMessage)
        self.web_source.parse_debug_received.connect(self.on_parse_debug_received)
        self.mock_sender.sent.connect(lambda text: self.statusBar().showMessage(f"Mock 已发送：{text}"))
        self.web_sender.sent.connect(lambda text: self.statusBar().showMessage(f"WebEngine 已发送：{text}"))
        self.web_sender.sent.connect(self.on_web_sent)
        self.web_sender.failed.connect(self.on_web_failed)
        self.web_sender.sendability_changed.connect(self.on_sendability_changed)
        self.web_view.loadFinished.connect(self.on_web_load_finished)
        self.mode_combo.currentIndexChanged.connect(self.on_mode_changed)
        self.source_combo.currentIndexChanged.connect(self.on_source_changed)
        self.pause_button.toggled.connect(self.on_pause_toggled)
        self.clear_button.clicked.connect(self.on_clear_logs)
        self.load_url_button.clicked.connect(self.load_web_url)
        self.load_demo_button.clicked.connect(self.load_demo_page)
        self.start_dom_button.clicked.connect(self._activate_web_source)
        self.send_selected_button.clicked.connect(self.send_selected_action)
        self.discard_selected_button.clicked.connect(self.discard_selected_action)
        self.send_quick_button.clicked.connect(self.send_quick_reply)
        self.like_once_button.clicked.connect(self.perform_live_like)
        self.auto_like_button.toggled.connect(self.on_auto_like_toggled)
        self.parse_debug_table.itemSelectionChanged.connect(self.on_parse_debug_selection_changed)

    @property
    def mode(self) -> LiveMode:
        return LiveMode(self.mode_combo.currentData())

    def on_mode_changed(self) -> None:
        self.session.mode = self.mode
        self.database.save_session(self.session)
        self.statusBar().showMessage(f"模式切换为 {MODE_LABELS[self.mode]}")

    def on_source_changed(self) -> None:
        if self.source_combo.currentData() == "webengine":
            self._activate_web_source()
        else:
            self._activate_mock_source()

    def _activate_mock_source(self) -> None:
        self.web_source.stop()
        self.login_status_timer.stop()
        self.auto_like_timer.stop()
        self.auto_like_button.setChecked(False)
        self.mock_source.start()
        self.source = self.mock_source
        self.sender = self.mock_sender
        self.login_status_label.setText("登录/发送状态：模拟发送可用")
        self.login_status_label.setStyleSheet("color: #267a36;")
        self.like_status_label.setText("点赞状态：WebEngine 未启用")
        self.like_status_label.setStyleSheet("color: #666;")
        self.source_combo.blockSignals(True)
        self.source_combo.setCurrentIndex(0)
        self.source_combo.blockSignals(False)
        self.statusBar().showMessage("已切换到模拟源")

    def _activate_web_source(self) -> None:
        self.mock_source.stop()
        self.web_source.start()
        self.web_sender.refresh_sendability()
        self.login_status_timer.start()
        self.source = self.web_source
        self.sender = self.web_sender
        self.source_combo.blockSignals(True)
        self.source_combo.setCurrentIndex(1)
        self.source_combo.blockSignals(False)
        self.statusBar().showMessage("已切换到 WebEngine DOM 观察源")

    def on_web_load_finished(self, ok: bool) -> None:
        if not ok:
            self.login_status_label.setText("登录/发送状态：页面加载失败")
            self.login_status_label.setStyleSheet("color: #b42318;")
            return
        self.web_source.reinstall()
        self.web_sender.refresh_sendability()

    def on_sendability_changed(self, can_send: bool, reason: str) -> None:
        prefix = "可发送" if can_send else "不可发送"
        color = "#267a36" if can_send else "#b42318"
        self.login_status_label.setText(f"登录/发送状态：{prefix} - {reason}")
        self.login_status_label.setStyleSheet(f"color: {color};")

    def on_web_sent(self, text: str) -> None:
        self.statusBar().showMessage(f"WebEngine 已发送：{text}")
        proposal = self._pending_action_by_text.pop(text, None)
        if proposal is not None:
            self.database.save_action(self.session.id, proposal, sent=True)

    def on_web_failed(self, reason: str) -> None:
        self.statusBar().showMessage(f"WebEngine 发送失败：{reason}")

    def load_web_url(self) -> None:
        room_or_url = self.url_input.text().strip()
        if not room_or_url:
            return
        self.web_view.setUrl(QUrl(self._douyin_live_url(room_or_url)))
        self._activate_web_source()

    def load_demo_page(self) -> None:
        demo_path = Path(__file__).resolve().parents[2] / "tools" / "demo_live_room.html"
        self.web_view.setUrl(QUrl.fromLocalFile(str(demo_path)))
        self.url_input.setText(str(demo_path))
        self._activate_web_source()

    def on_pause_toggled(self, checked: bool) -> None:
        self.auto_paused = checked
        self.pause_button.setText("恢复自动发送" if checked else "暂停自动发送")
        self.statusBar().showMessage("自动发送已暂停" if checked else "自动发送已恢复")

    def on_event(self, event: LiveEvent) -> None:
        self.database.save_event(event)
        self._append_event(event)
        song = self.song_service.maybe_add_from_event(event)
        if song:
            self.refresh_songs()

        risks = self.risk_detector.detect(event)
        for hit in risks:
            self._append_action(
                ActionProposal(
                    event_id=event.id,
                    rule_id="risk",
                    rule_name=f"风险提示：{hit.label}",
                    text=f"{event.display_user}: {hit.reason}",
                    auto_send=False,
                )
            )

        proposals = self.rule_engine.evaluate(event, self.mode)
        gift_proposal = self.gift_strategy.build(event)
        if gift_proposal:
            proposals.append(gift_proposal)

        for proposal in proposals:
            self._append_action(proposal)
            if proposal.auto_send:
                self._try_send(proposal)

    def _append_event(self, event: LiveEvent) -> None:
        row = self.event_table.rowCount()
        self.event_table.insertRow(row)
        values = [
            datetime.now().strftime("%H:%M:%S"),
            event.label,
            event.display_user,
            event.display_content,
        ]
        for col, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            if event.type.value == "chat" and any(word in event.display_content for word in ["带节奏", "中之人", "滚"]):
                item.setBackground(Qt.GlobalColor.yellow)
            self.event_table.setItem(row, col, item)
        self.event_table.scrollToBottom()

    def on_parse_debug_received(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        raw = payload.get("raw") if isinstance(payload.get("raw"), dict) else {}
        parsed = payload.get("parsed") if isinstance(payload.get("parsed"), dict) else None
        labels = raw.get("mediaLabels") if isinstance(raw.get("mediaLabels"), list) else []
        parsed_summary = ""
        if parsed:
            parsed_summary = " | ".join(
                str(value)
                for value in [parsed.get("type"), parsed.get("username"), parsed.get("content")]
                if value
            )

        row = self.parse_debug_table.rowCount()
        self.parse_debug_table.insertRow(row)
        values = [
            datetime.now().strftime("%H:%M:%S"),
            raw.get("type") or "",
            raw.get("text") or raw.get("content") or "",
            ", ".join(str(label) for label in labels),
            parsed_summary or "未解析",
        ]
        for col, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            item.setData(Qt.ItemDataRole.UserRole, payload)
            self.parse_debug_table.setItem(row, col, item)
        self.parse_debug_table.scrollToBottom()
        self._trim_table(self.parse_debug_table, 300)

    def on_parse_debug_selection_changed(self) -> None:
        items = self.parse_debug_table.selectedItems()
        if not items:
            return
        payload = items[0].data(Qt.ItemDataRole.UserRole)
        if isinstance(payload, dict):
            self.parse_debug_detail.setPlainText(json.dumps(payload, ensure_ascii=False, indent=2))

    def _append_action(self, proposal: ActionProposal) -> None:
        self.database.save_action(self.session.id, proposal, sent=False)
        item = QListWidgetItem(f"[{proposal.rule_name}] {proposal.text}")
        item.setData(Qt.ItemDataRole.UserRole, proposal)
        self.action_list.addItem(item)
        self.action_list.scrollToBottom()

    def _try_send(self, proposal: ActionProposal) -> bool:
        if self.auto_paused:
            return False
        if self.sender is self.web_sender and not self.web_sender.can_send:
            self.statusBar().showMessage(f"WebEngine 当前不可发送：{self.web_sender.status_reason}")
            self.web_sender.refresh_sendability()
            return False
        if not self.limiter.allow():
            self.statusBar().showMessage("全局限频生效：本分钟自动发送额度已用完")
            return False
        if self.sender is self.web_sender:
            self._pending_action_by_text[proposal.text] = proposal
            return self.sender.send(proposal.text)
        sent = self.sender.send(proposal.text)
        if sent:
            self.database.save_action(self.session.id, proposal, sent=True)
        return sent

    def send_selected_action(self) -> None:
        item = self.action_list.currentItem()
        if item is None:
            return
        proposal = item.data(Qt.ItemDataRole.UserRole)
        if proposal and self._try_send(proposal):
            item.setText(f"已发送 | {item.text()}")

    def discard_selected_action(self) -> None:
        row = self.action_list.currentRow()
        if row >= 0:
            self.action_list.takeItem(row)

    def send_quick_reply(self) -> None:
        item = self.quick_replies.currentItem()
        if item is None:
            return
        proposal = ActionProposal(
            event_id="manual",
            rule_id="quick-reply",
            rule_name="快捷回复",
            text=item.text(),
            auto_send=False,
        )
        self._append_action(proposal)
        self._try_send(proposal)

    def on_auto_like_toggled(self, checked: bool) -> None:
        if checked:
            if self.source is not self.web_source:
                self._activate_web_source()
            self.auto_like_timer.start()
            self.like_status_label.setText("点赞状态：自动低频点赞已启动")
            self.like_status_label.setStyleSheet("color: #267a36;")
            self.perform_live_like()
        else:
            self.auto_like_timer.stop()
            self.like_status_label.setText("点赞状态：已停止")
            self.like_status_label.setStyleSheet("color: #666;")

    def perform_live_like(self) -> None:
        if self.source is not self.web_source:
            self.like_status_label.setText("点赞状态：请先切到 WebEngine")
            self.like_status_label.setStyleSheet("color: #b42318;")
            return
        script = """
        (() => {
          const visible = (node) => {
            if (!node) return false;
            const style = getComputedStyle(node);
            const rect = node.getBoundingClientRect();
            return style.display !== "none" && style.visibility !== "hidden" && rect.width > 80 && rect.height > 80;
          };
          const candidates = [
            ...document.querySelectorAll("video"),
            ...document.querySelectorAll("[class*='player'], [class*='Player'], [class*='live'], [class*='Live']")
          ].filter(visible);
          candidates.sort((a, b) => {
            const ar = a.getBoundingClientRect();
            const br = b.getBoundingClientRect();
            return (br.width * br.height) - (ar.width * ar.height);
          });
          const node = candidates[0] || document.elementFromPoint(window.innerWidth * 0.38, window.innerHeight * 0.45);
          if (!node) return JSON.stringify({ ok: false, reason: "未找到直播画面区域" });
          const rect = node.getBoundingClientRect();
          return JSON.stringify({
            ok: true,
            x: Math.round(rect.left + rect.width * 0.5),
            y: Math.round(rect.top + rect.height * 0.5),
            tagName: node.tagName,
            className: String(node.className || "").slice(0, 80)
          });
        })();
        """
        self.web_view.page().runJavaScript(script, self._handle_like_target)

    def _handle_like_target(self, result: object) -> None:
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError:
                result = None
        if not isinstance(result, dict) or not result.get("ok"):
            reason = result.get("reason") if isinstance(result, dict) else "点赞定位失败"
            self.like_status_label.setText(f"点赞状态：{reason}")
            self.like_status_label.setStyleSheet("color: #b42318;")
            return
        point = QPoint(int(result.get("x", 0)), int(result.get("y", 0)))
        QTest.mouseClick(self.web_view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
        QTest.qWait(80)
        QTest.mouseClick(self.web_view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
        self.like_status_label.setText(f"点赞状态：已双击直播画面 {datetime.now().strftime('%H:%M:%S')}")
        self.like_status_label.setStyleSheet("color: #267a36;")

    def refresh_songs(self) -> None:
        songs = self.song_repository.list_for_session(self.session.id)
        self.song_table.setRowCount(0)
        for song in songs:
            row = self.song_table.rowCount()
            self.song_table.insertRow(row)
            values = [self._song_status_label(song.status), song.song_title, song.requested_by, song.note]
            for col, value in enumerate(values):
                self.song_table.setItem(row, col, QTableWidgetItem(value))

    def on_clear_logs(self) -> None:
        answer = QMessageBox.question(self, "清空日志", "确定清空事件和动作日志吗？歌单不会删除。")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.database.clear_logs()
        self.event_table.setRowCount(0)
        self.parse_debug_table.setRowCount(0)
        self.parse_debug_detail.clear()
        self.action_list.clear()
        self.statusBar().showMessage("事件和动作日志已清空")

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.mock_source.stop()
        self.web_source.stop()
        self.login_status_timer.stop()
        self.auto_like_timer.stop()
        self.session.end()
        self.database.save_session(self.session)
        super().closeEvent(event)

    @staticmethod
    def _song_status_label(status: SongStatus) -> str:
        return {
            SongStatus.PENDING: "待唱",
            SongStatus.CURRENT: "当前",
            SongStatus.DONE: "已唱",
            SongStatus.SKIPPED: "跳过",
            SongStatus.REJECTED: "拒绝",
        }[status]

    @staticmethod
    def _trim_table(table: QTableWidget, max_rows: int) -> None:
        while table.rowCount() > max_rows:
            table.removeRow(0)

    @staticmethod
    def _douyin_live_url(room_or_url: str) -> str:
        value = room_or_url.strip()
        if "://" in value:
            return value
        room_id = value.removeprefix("live.douyin.com/").strip("/")
        return f"https://live.douyin.com/{room_id}"
