from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QPoint, QUrl, Qt
from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QPixmap, QAction
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PySide6.QtTest import QTest
from PySide6.QtWebEngineCore import QWebEngineUrlRequestInterceptor, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView


class _StreamUrlInterceptor(QWebEngineUrlRequestInterceptor):
    """Intercepts all network requests to detect stream URLs."""

    def __init__(self, on_stream_url):
        super().__init__()
        self._on_stream_url = on_stream_url
        self._seen: set[str] = set()
        self._best_url: str | None = None
        self._best_score: int = -1

    @staticmethod
    def _score_url(url: str) -> int:
        lower = url.lower()
        if "only_audio" in lower:
            return -1
        if "_or4" in lower:
            return 5
        if "_uhd" in lower:
            return 4
        if "_hd" in lower:
            return 3
        if "_md" in lower:
            return 2
        if "_sd" in lower:
            return 1
        if "_ld" in lower:
            return 0
        return 3

    def interceptRequest(self, info):
        url = info.requestUrl().toString()
        lower = url.lower()
        # Log interesting requests to stderr
        if any(kw in lower for kw in ("flv", "m3u8", "webm", "pull-hs", "pull-hl",
                                       "pull-c6", "pull-c3", "stream",
                                       "douyin", "amemv", "byteimg",
                                       "pstatp.com", "bytegoofy.com",
                                       "snssdk.com", "ixigua.com")):
            import sys
            sys.stderr.write(f"[DMM-REQ] {url[:200]}\n")
            sys.stderr.flush()
        # Detect actual stream URLs and pick best quality
        if any(ext in lower for ext in (".flv", ".m3u8", "flv?", "flv&")):
            if url not in self._seen:
                self._seen.add(url)
                score = self._score_url(url)
                if score > self._best_score:
                    self._best_score = score
                    self._best_url = url
                    self._on_stream_url(url)


from PySide6.QtWidgets import (
    QComboBox,
    QCheckBox,
    QMenu,
    QMenuBar,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QSpinBox,
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
from douyin_mod_manager.features.song_queue import SongQueueService
from douyin_mod_manager.senders.mock import MockMessageSender
from douyin_mod_manager.senders.webengine import WebEngineMessageSender
from douyin_mod_manager.sources.mock import MockEventSource
from douyin_mod_manager.sources.gift_images import GiftImageRegistry, gift_image_key
from douyin_mod_manager.sources.ffmpeg_proxy import FFmpegStreamProxy
from douyin_mod_manager.sources.webengine_dom import WebEngineDomEventSource
from douyin_mod_manager.storage.database import Database
from douyin_mod_manager.storage.repositories import SongRepository, SongStatus


class MainWindow(QMainWindow):
    _sig_create_overlay = Signal()

    def __init__(self, database: Database) -> None:
        super().__init__()
        self.database = database
        self.session = LiveSession(name=f"虚拟主播场次 {datetime.now().strftime('%H:%M')}")
        self.database.save_session(self.session)

        self.rule_engine = RuleEngine()
        self.song_repository = SongRepository(database)
        self.song_service = SongQueueService(self.song_repository)
        self.gift_strategy = GiftThanksStrategy()
        self.limiter = SlidingWindowLimiter(max_events=3, window_seconds=60)
        self.auto_paused = True
        self._pending_action_by_text: dict[str, ActionProposal] = {}
        self.source = None
        self.gift_image_registry = GiftImageRegistry()
        self._refreshing_gift_image_table = False
        self._gift_sort_by = "name"
        self._all_events: list[tuple[str, LiveEvent]] = []
        self._ffmpeg_proxy = FFmpegStreamProxy(on_status=self.statusBar().showMessage)
        self.gift_image_cache_dir = Path(__file__).resolve().parents[2] / "data" / "gift_image_cache"
        self.gift_image_network = QNetworkAccessManager(self)
        self._pending_gift_image_labels: dict[str, list[QLabel]] = {}
        self._pending_gift_image_urls: dict[str, str] = {}

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
        self._like_click_timer = QTimer(self)
        self._like_click_timer.timeout.connect(self._do_like_click)
        self._like_duration_timer = QTimer(self)
        self._like_duration_timer.setSingleShot(True)
        self._like_duration_timer.timeout.connect(self._stop_like_click)
        self._danmaku_timer = QTimer(self)
        self._danmaku_timer.timeout.connect(self._send_danmaku)
        self._danmaku_index = 0

        self._connect_signals()
        self._activate_web_source()
        self.statusBar().showMessage("WebEngine DOM 观察源已就绪，点击加载后进入直播间；自动发送默认暂停")
        QTimer.singleShot(1000, self.load_web_url)

    def _log_debug(self, msg: str) -> None:
        import sys
        sys.stderr.write(f"[DMM] {msg}\n")
        sys.stderr.flush()

    def _build_menu_bar(self) -> None:
        menubar = QMenuBar(self)
        menubar.setNativeMenuBar(False)
        self.setMenuBar(menubar)

        file_menu = menubar.addMenu("文件(&F)")
        file_menu.addAction(QAction("导出当前场次日志(&E)", self, triggered=self._export_session_log))
        file_menu.addAction(QAction("清空日志(&C)", self, triggered=self.on_clear_logs))
        file_menu.addSeparator()
        file_menu.addAction(QAction("退出(&Q)", self, triggered=self.close, shortcut="Ctrl+Q"))

        view_menu = menubar.addMenu("视图(&V)")
        self._debug_mode_action = QAction("调试模式(&D)", self, checkable=True, shortcut="Ctrl+D")
        self._debug_mode_action.toggled.connect(self._set_debug_mode)
        view_menu.addAction(self._debug_mode_action)

        help_menu = menubar.addMenu("帮助(&H)")
        help_menu.addAction(QAction("关于(&A)", self, triggered=self._show_about))

    def _set_debug_mode(self, enabled: bool) -> None:
        self.load_demo_button.setVisible(enabled)
        self.diagnose_color_button.setVisible(enabled)
        self._tabs.setTabVisible(3, enabled)

    def _show_about(self) -> None:
        QMessageBox.about(self, "关于", "抖音虚拟主播房管管理器\n本地原型 v0.1\n\n基于 PySide6 + Qt WebEngine")

    def _build_ui(self) -> None:
        self._build_menu_bar()

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

        self.pause_button = QPushButton("恢复自动发送")
        self.pause_button.setCheckable(True)
        self.pause_button.setChecked(True)
        toolbar.addWidget(self.pause_button)

        self.clear_button = QPushButton("清空日志")
        toolbar.addWidget(self.clear_button)

        self.hide_enter_events_checkbox = QCheckBox("隐藏进场")
        self.hide_enter_events_checkbox.setChecked(True)
        toolbar.addWidget(self.hide_enter_events_checkbox)

        self.hide_like_events_checkbox = QCheckBox("隐藏点赞")
        self.hide_like_events_checkbox.setChecked(False)
        toolbar.addWidget(self.hide_like_events_checkbox)

        self.transcode_button = QPushButton("转码画面")
        self.transcode_button.setCheckable(True)
        toolbar.addWidget(self.transcode_button)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.addWidget(self._build_web_placeholder())
        self._event_tabs_widget = self._build_event_tabs()
        self._splitter.addWidget(self._event_tabs_widget)
        self._splitter.addWidget(self._build_action_panel())
        self._splitter.setStretchFactor(0, 3)
        self._splitter.setStretchFactor(1, 3)
        self._splitter.setStretchFactor(2, 2)
        self.setCentralWidget(self._splitter)
        self.setStatusBar(QStatusBar())

    def _build_web_placeholder(self) -> QWidget:
        panel = QWidget()
        panel.setMinimumWidth(360)
        layout = QVBoxLayout(panel)

        title = QLabel("WebEngine 页面区")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        layout.addWidget(title)

        controls = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setText("88162227205")
        self.url_input.setPlaceholderText("输入抖音直播房间号")
        self.load_url_button = QPushButton("加载")
        self.load_demo_button = QPushButton("Demo")
        self.load_demo_button.setVisible(False)
        self.start_dom_button = QPushButton("启动观察")
        self.diagnose_color_button = QPushButton("诊断颜色")
        self.diagnose_color_button.setVisible(False)
        controls.addWidget(self.url_input, 1)
        controls.addWidget(self.load_url_button)
        controls.addWidget(self.load_demo_button)
        controls.addWidget(self.start_dom_button)
        controls.addWidget(self.diagnose_color_button)
        layout.addLayout(controls)

        self.login_status_label = QLabel("登录/发送状态：自动检测中")
        self.login_status_label.setStyleSheet("color: #666;")
        layout.addWidget(self.login_status_label)

        # Container: web_view and overlay side by side, orientation set dynamically
        self._web_container = QWidget()
        self._web_layout = QHBoxLayout(self._web_container)
        self._web_layout.setContentsMargins(0, 0, 0, 0)
        self._web_layout.setSpacing(2)
        self._overlay_aspect = 360 / 640  # default portrait, updated on meta

        self.web_view = QWebEngineView()
        self.web_view.setZoomFactor(0.5)
        self.web_view.setHtml(
            """
            <html><body style="font-family: -apple-system; padding: 24px;">
              <h2>WebEngine 页面区</h2>
              <p>加载本地 demo 或你手动登录后的直播间页面。</p>
              <p>DOM 观察器只读取页面上已经展示的内容。</p>
            </body></html>
            """
        )
        # Install network request interceptor
        self._stream_interceptor = _StreamUrlInterceptor(self._on_intercepted_stream_url)
        profile = self.web_view.page().profile()
        profile.setUrlRequestInterceptor(self._stream_interceptor)
        self._web_layout.addWidget(self.web_view, 1)

        # Overlay container: holds the transcode player widget, hidden by default
        # Not added to any layout initially; placed dynamically on show/hide
        self._overlay_container = QWidget()
        self._overlay_container.setVisible(False)
        self._overlay_layout = QVBoxLayout(self._overlay_container)
        self._overlay_layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(self._web_container, 1)
        return panel

    def _build_event_tabs(self) -> QWidget:
        self._tabs = QTabWidget()
        self._tabs.setMinimumWidth(300)
        tabs = self._tabs
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
        tabs.setTabVisible(tabs.count() - 1, False)

        gift_panel = QWidget()
        gift_layout = QVBoxLayout(gift_panel)
        self.gift_image_table = QTableWidget(0, 7)
        self.gift_image_table.setHorizontalHeaderLabels(["图片", "状态", "图片指纹", "礼物名", "钻石", "出现次数", "最后出现"])
        self.gift_image_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        gift_layout.addWidget(self.gift_image_table, 1)
        self.gift_image_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.gift_image_table.customContextMenuRequested.connect(self._gift_table_context_menu)
        gift_buttons = QHBoxLayout()
        self.refresh_gift_images_button = QPushButton("刷新映射")
        self.clear_gift_name_button = QPushButton("清空选中名称")
        self.sort_by_name_button = QPushButton("按名称")
        self.sort_by_diamond_button = QPushButton("按钻石")
        self.sort_by_seen_button = QPushButton("按出现次数")
        gift_buttons.addWidget(self.refresh_gift_images_button)
        gift_buttons.addWidget(self.clear_gift_name_button)
        gift_buttons.addWidget(QLabel("排序:"))
        gift_buttons.addWidget(self.sort_by_name_button)
        gift_buttons.addWidget(self.sort_by_diamond_button)
        gift_buttons.addWidget(self.sort_by_seen_button)
        gift_buttons.addStretch(1)
        gift_layout.addLayout(gift_buttons)
        tabs.addTab(gift_panel, "礼物映射")
        self.refresh_gift_image_table()
        return tabs

    def _build_action_panel(self) -> QWidget:
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(4, 4, 4, 4)

        layout.addWidget(QLabel("待确认回复 / 自动动作"))
        self.action_list = QListWidget()
        self.action_list.setMinimumHeight(120)
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
        ]:
            self.quick_replies.addItem(text)
        self.quick_replies.setEditTriggers(QListWidget.EditTrigger.DoubleClicked)
        layout.addWidget(self.quick_replies, 1)
        quick_buttons = QHBoxLayout()
        self.add_quick_button = QPushButton("添加")
        self.del_quick_button = QPushButton("删除")
        self.send_quick_button = QPushButton("发送选中")
        quick_buttons.addWidget(self.add_quick_button)
        quick_buttons.addWidget(self.del_quick_button)
        quick_buttons.addWidget(self.send_quick_button)
        layout.addLayout(quick_buttons)

        layout.addWidget(QLabel("直播间连点"))
        like_controls = QHBoxLayout()
        like_controls.addWidget(QLabel("间隔(ms)"))
        self.like_interval_spin = QSpinBox()
        self.like_interval_spin.setRange(50, 5000)
        self.like_interval_spin.setValue(100)
        like_controls.addWidget(self.like_interval_spin)
        like_controls.addWidget(QLabel("时长(秒)"))
        self.like_duration_spin = QSpinBox()
        self.like_duration_spin.setRange(1, 60)
        self.like_duration_spin.setValue(5)
        like_controls.addWidget(self.like_duration_spin)
        self.like_click_button = QPushButton("开始连点")
        self.like_click_button.setCheckable(True)
        like_controls.addWidget(self.like_click_button)
        layout.addLayout(like_controls)
        self.like_status_label = QLabel("连点状态：未启动")
        self.like_status_label.setStyleSheet("color: #666;")
        layout.addWidget(self.like_status_label)

        layout.addWidget(QLabel("循环弹幕"))
        self.danmaku_list = QListWidget()
        self.danmaku_list.setEditTriggers(QListWidget.EditTrigger.DoubleClicked)
        self.danmaku_list.setMaximumHeight(80)
        for text in ["点歌请发送：点歌 歌名", "感谢大家的观看！"]:
            item = QListWidgetItem(text)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
            self.danmaku_list.addItem(item)
        layout.addWidget(self.danmaku_list)
        danmaku_list_buttons = QHBoxLayout()
        self.danmaku_add_button = QPushButton("添加")
        self.danmaku_del_button = QPushButton("删除")
        danmaku_list_buttons.addWidget(self.danmaku_add_button)
        danmaku_list_buttons.addWidget(self.danmaku_del_button)
        layout.addLayout(danmaku_list_buttons)
        danmaku_controls = QHBoxLayout()
        danmaku_controls.addWidget(QLabel("模式"))
        self.danmaku_mode_combo = QComboBox()
        self.danmaku_mode_combo.addItem("顺序", "sequential")
        self.danmaku_mode_combo.addItem("随机", "random")
        danmaku_controls.addWidget(self.danmaku_mode_combo)
        danmaku_controls.addWidget(QLabel("间隔(秒)"))
        self.danmaku_interval_spin = QSpinBox()
        self.danmaku_interval_spin.setRange(1, 300)
        self.danmaku_interval_spin.setValue(5)
        danmaku_controls.addWidget(self.danmaku_interval_spin)
        self.danmaku_start_button = QPushButton("开始弹幕")
        self.danmaku_start_button.setCheckable(True)
        danmaku_controls.addWidget(self.danmaku_start_button)
        layout.addLayout(danmaku_controls)
        self.danmaku_status_label = QLabel("弹幕状态：未启动")
        self.danmaku_status_label.setStyleSheet("color: #666;")
        layout.addWidget(self.danmaku_status_label)

        scroll = QScrollArea()
        scroll.setWidget(inner)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(260)
        return scroll

    def _connect_signals(self) -> None:
        self.mock_source.event_received.connect(lambda event: self.on_source_event(event, "mock"))
        self.mock_source.status_changed.connect(self.statusBar().showMessage)
        self.web_source.event_received.connect(lambda event: self.on_source_event(event, "webengine"))
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
        self.hide_enter_events_checkbox.toggled.connect(self._rebuild_event_table)
        self.hide_like_events_checkbox.toggled.connect(self._rebuild_event_table)
        self.load_url_button.clicked.connect(self.load_web_url)
        self.load_demo_button.clicked.connect(self.load_demo_page)
        self.start_dom_button.clicked.connect(self._activate_web_source)
        self.diagnose_color_button.clicked.connect(self._diagnose_colors)
        self.send_selected_button.clicked.connect(self.send_selected_action)
        self.discard_selected_button.clicked.connect(self.discard_selected_action)
        self.send_quick_button.clicked.connect(self.send_quick_reply)
        self.add_quick_button.clicked.connect(self._add_quick_reply)
        self.del_quick_button.clicked.connect(self._del_quick_reply)
        self.like_click_button.toggled.connect(self.on_like_click_toggled)
        self.danmaku_add_button.clicked.connect(self._add_danmaku)
        self.danmaku_del_button.clicked.connect(self._del_danmaku)
        self.danmaku_start_button.toggled.connect(self.on_danmaku_toggled)
        self.transcode_button.toggled.connect(self.on_transcode_toggled)
        self._sig_create_overlay.connect(self._create_transcode_overlay)
        self.parse_debug_table.itemSelectionChanged.connect(self.on_parse_debug_selection_changed)
        self.gift_image_table.cellChanged.connect(self.on_gift_image_cell_changed)
        self.refresh_gift_images_button.clicked.connect(self.refresh_gift_image_table)
        self.clear_gift_name_button.clicked.connect(self.clear_selected_gift_name)
        self.sort_by_name_button.clicked.connect(lambda: self.refresh_gift_image_table("name"))
        self.sort_by_diamond_button.clicked.connect(lambda: self.refresh_gift_image_table("diamond"))
        self.sort_by_seen_button.clicked.connect(lambda: self.refresh_gift_image_table("seen"))

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
        self._stop_like_click()
        self._stop_danmaku()
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
        if self._all_events:
            self._export_session_log()
        self.session.end()
        self.database.save_session(self.session)
        self._all_events.clear()
        self.event_table.setRowCount(0)
        self.action_list.clear()
        self.session = LiveSession(name=f"虚拟主播场次 {datetime.now().strftime('%H:%M')}")
        self.database.save_session(self.session)
        self.web_view.setUrl(QUrl(self._douyin_live_url(room_or_url)))
        self._activate_web_source()
        QTimer.singleShot(2000, self.start_dom_button.click)
        # Poll until page URL matches live.douyin.com, then auto-transcode
        self._wait_for_page_count = 0
        self._wait_for_page_timer = QTimer(self)
        self._wait_for_page_timer.timeout.connect(self._check_page_ready)
        self._wait_for_page_timer.start(2000)

    def _check_page_ready(self) -> None:
        self._wait_for_page_count += 1
        if self._wait_for_page_count > 15:  # 30s max
            self._wait_for_page_timer.stop()
            self._log_debug("page ready check timed out")
            return
        self.web_view.page().runJavaScript(
            "window.location.href",
            lambda url: self._on_page_url_check(str(url or "").strip()),
        )

    def _on_page_url_check(self, url: str) -> None:
        self._log_debug(f"page poll #{self._wait_for_page_count}: {url[:80]}")
        if "live.douyin.com/" in url and not self.transcode_button.isChecked():
            self._wait_for_page_timer.stop()
            self._log_debug(f"page ready, auto-starting transcode")
            QTimer.singleShot(1000, self.transcode_button.click)

    def load_demo_page(self) -> None:
        demo_path = Path(__file__).resolve().parents[2] / "tools" / "demo_live_room.html"
        self.web_view.setUrl(QUrl.fromLocalFile(str(demo_path)))
        self.url_input.setText(str(demo_path))
        self._activate_web_source()

    def _diagnose_colors(self) -> None:
        script = """
        (() => {
          const items = document.querySelectorAll('.webcast-chatroom___item');
          const results = [];
          for (const item of Array.from(items).slice(-20)) {
            const style = window.getComputedStyle(item);
            const children = Array.from(item.querySelectorAll('*'));
            const childColors = children.map(c => {
              const s = window.getComputedStyle(c);
              return {
                tag: c.tagName,
                cls: (c.className || '').toString().slice(0, 80),
                text: (c.innerText || c.textContent || '').trim().slice(0, 40),
                color: s.color || '',
                bg: s.backgroundColor || '',
                inlineStyle: (c.getAttribute('style') || '').slice(0, 200)
              };
            });
            results.push({
              text: (item.innerText || '').trim().slice(0, 80),
              color: style.color || '',
              bg: style.backgroundColor || '',
              childCount: children.length,
              children: childColors.filter(c => c.color !== 'rgb(0, 0, 0)' || c.bg !== 'rgba(0, 0, 0, 0)' || c.inlineStyle.length > 0)
            });
          }
          return JSON.stringify(results);
        })();
        """
        self.web_view.page().runJavaScript(script, self._handle_color_diagnosis)

    def _handle_color_diagnosis(self, result: object) -> None:
        if not isinstance(result, str):
            self.statusBar().showMessage("诊断结果为空")
            return
        try:
            items = json.loads(result)
        except json.JSONDecodeError:
            self.statusBar().showMessage("诊断结果解析失败")
            return
        lines = []
        for item in items:
            lines.append(f"文本: {item.get('text', '')[:50]}")
            lines.append(f"  容器 color={item.get('color', '')} bg={item.get('bg', '')}")
            children = item.get('children', [])
            if children:
                for ch in children:
                    lines.append(f"  子 {ch.get('tag','')} color={ch.get('color','')} bg={ch.get('bg','')} style={ch.get('inlineStyle','')} text={ch.get('text','')[:30]}")
            else:
                lines.append(f"  (无特殊颜色子元素, 总子元素数={item.get('childCount', 0)})")
            lines.append("")
        diag_path = Path(__file__).resolve().parents[2] / "data" / "color_diagnosis.txt"
        diag_path.parent.mkdir(parents=True, exist_ok=True)
        diag_path.write_text("\n".join(lines), encoding="utf-8")
        self.statusBar().showMessage(f"颜色诊断已写入 {diag_path}")

    def on_pause_toggled(self, checked: bool) -> None:
        self.auto_paused = checked
        self.pause_button.setText("恢复自动发送" if checked else "暂停自动发送")
        self.statusBar().showMessage("自动发送已暂停" if checked else "自动发送已恢复")

    def on_source_event(self, event: LiveEvent, source_name: str) -> None:
        self.on_event(event)

    def _should_show_event(self, event: LiveEvent) -> bool:
        if event.type.value == "user_enter" and self.hide_enter_events_checkbox.isChecked():
            return False
        if event.type.value == "system" and "点赞" in event.display_content and self.hide_like_events_checkbox.isChecked():
            return False
        return True

    def _rebuild_event_table(self) -> None:
        self.event_table.setRowCount(0)
        for ts, event in self._all_events:
            if self._should_show_event(event):
                self._append_event(event, ts)

    def on_event(self, event: LiveEvent) -> None:
        self.database.save_event(event)
        ts = datetime.now().strftime("%H:%M:%S")
        self._all_events.append((ts, event))
        if self._should_show_event(event):
            self._append_event(event, ts)
        song = self.song_service.maybe_add_from_event(event)
        if song:
            self.refresh_songs()

        proposals = self.rule_engine.evaluate(event, self.mode)
        gift_proposal = self.gift_strategy.build(event)
        if gift_proposal:
            proposals.append(gift_proposal)

        for proposal in proposals:
            self._append_action(proposal)
            if proposal.auto_send:
                self._try_send(proposal)

    def _append_event(self, event: LiveEvent, ts: str | None = None) -> None:
        row = self.event_table.rowCount()
        self.event_table.insertRow(row)
        values = [
            ts or datetime.now().strftime("%H:%M:%S"),
            event.label,
            event.display_user,
            event.display_content,
        ]
        for col, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            self.event_table.setItem(row, col, item)
        self.event_table.scrollToBottom()

    def on_parse_debug_received(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        raw = payload.get("raw") if isinstance(payload.get("raw"), dict) else {}
        parsed = payload.get("parsed") if isinstance(payload.get("parsed"), dict) else None
        gift_name = parsed.get("gift_name") if isinstance(parsed, dict) and isinstance(parsed.get("gift_name"), str) else None
        if self.gift_image_registry.record_raw(raw, gift_name):
            self.refresh_gift_image_table(self._gift_sort_by)
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

    def refresh_gift_image_table(self, sort_by: str = "name") -> None:
        self.gift_image_registry.load()
        self._gift_sort_by = sort_by
        self._refreshing_gift_image_table = True
        self.gift_image_table.closePersistentEditor(self.gift_image_table.currentItem())
        self.gift_image_table.setRowCount(0)
        for entry in self.gift_image_registry.list_entries(sort_by=sort_by):
            row = self.gift_image_table.rowCount()
            self.gift_image_table.insertRow(row)
            self.gift_image_table.setRowHeight(row, 58)
            self.gift_image_table.setCellWidget(row, 0, self._gift_image_preview(entry.image_url))
            values = [
                entry.status,
                entry.image_key,
                entry.name,
                str(entry.diamond_count) if entry.diamond_count else "",
                str(entry.seen_count),
                entry.last_seen,
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, entry.image_key)
                table_col = col + 1
                if table_col not in (3, 4):
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.gift_image_table.setItem(row, table_col, item)
        self._refreshing_gift_image_table = False

    def on_gift_image_cell_changed(self, row: int, col: int) -> None:
        if self._refreshing_gift_image_table or col not in (3, 4):
            return
        item = self.gift_image_table.item(row, col)
        key_item = self.gift_image_table.item(row, 2)
        if item is None or key_item is None:
            return
        image_key = str(key_item.data(Qt.ItemDataRole.UserRole) or key_item.text()).strip()
        if not image_key:
            return
        if col == 3:
            self.gift_image_registry.set_name(image_key, item.text())
        else:
            try:
                value = int(item.text()) if item.text().strip() else 0
            except ValueError:
                value = 0
            self.gift_image_registry.set_diamond_count(image_key, value)
        self.refresh_gift_image_table(self._gift_sort_by)
        self.statusBar().showMessage("礼物图片映射已保存")

    def clear_selected_gift_name(self) -> None:
        row = self.gift_image_table.currentRow()
        if row < 0:
            return
        key_item = self.gift_image_table.item(row, 2)
        if key_item is None:
            return
        image_key = str(key_item.data(Qt.ItemDataRole.UserRole) or key_item.text()).strip()
        if not image_key:
            return
        self.gift_image_registry.set_name(image_key, "")
        self.refresh_gift_image_table(self._gift_sort_by)
        self.statusBar().showMessage("礼物图片映射名称已清空")

    def _gift_table_context_menu(self, pos) -> None:
        row = self.gift_image_table.rowAt(pos.y())
        if row < 0:
            return
        self.gift_image_table.setCurrentCell(row, 2)
        menu = QMenu(self)
        delete_action = menu.addAction("删除此行")
        action = menu.exec(self.gift_image_table.viewport().mapToGlobal(pos))
        if action == delete_action:
            self._delete_gift_row(row)

    def _delete_gift_row(self, row: int) -> None:
        key_item = self.gift_image_table.item(row, 2)
        if key_item is None:
            return
        image_key = str(key_item.data(Qt.ItemDataRole.UserRole) or key_item.text()).strip()
        if not image_key:
            return
        name_item = self.gift_image_table.item(row, 3)
        display_name = name_item.text().strip() if name_item else ""
        label = f"{display_name} ({image_key[:12]}…)" if display_name else image_key[:20]
        answer = QMessageBox.question(self, "删除礼物映射", f"确定删除 {label} 吗？")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.gift_image_registry.delete_entry(image_key)
        self.refresh_gift_image_table(self._gift_sort_by)
        self.statusBar().showMessage(f"已删除礼物映射 {label}")

    def _gift_image_preview(self, image_url: str) -> QLabel:
        label = QLabel()
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setMinimumSize(52, 52)
        label.setMaximumSize(64, 64)
        pixmap = self._gift_image_pixmap(image_url)
        if pixmap is None:
            label.setText("加载中" if image_url else "无图")
            label.setStyleSheet("color: #888;")
            if image_url:
                self._queue_gift_image_download(image_url, label)
        else:
            label.setPixmap(pixmap.scaled(48, 48, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        return label

    def _gift_image_pixmap(self, image_url: str) -> QPixmap | None:
        if not image_url:
            return None
        cache_path = self._gift_image_cache_path(image_url)
        if cache_path and cache_path.exists():
            pixmap = QPixmap(str(cache_path))
            if not pixmap.isNull():
                return pixmap
        return None

    def _gift_image_cache_path(self, image_url: str) -> Path | None:
        image_key = gift_image_key(image_url)
        if not image_key:
            return None
        image_key = "".join(char if char.isalnum() or char in "-_" else "_" for char in image_key)
        suffix = Path(image_url.split("?", 1)[0].split("~", 1)[0]).suffix.lower() or ".png"
        if suffix not in {".png", ".webp", ".jpg", ".jpeg"}:
            suffix = ".png"
        return self.gift_image_cache_dir / f"{image_key}{suffix}"

    def _queue_gift_image_download(self, image_url: str, label: QLabel) -> None:
        cache_path = self._gift_image_cache_path(image_url)
        if cache_path is None:
            label.setText("无图")
            return
        image_key = cache_path.stem
        self._pending_gift_image_labels.setdefault(image_key, []).append(label)
        if image_key in self._pending_gift_image_urls:
            return
        self._pending_gift_image_urls[image_key] = image_url
        request = QNetworkRequest(QUrl(image_url))
        request.setRawHeader(b"User-Agent", b"Mozilla/5.0")
        reply = self.gift_image_network.get(request)
        reply.finished.connect(lambda reply=reply, image_key=image_key, cache_path=cache_path: self._on_gift_image_downloaded(reply, image_key, cache_path))

    def _on_gift_image_downloaded(self, reply, image_key: str, cache_path: Path) -> None:
        labels = self._pending_gift_image_labels.pop(image_key, [])
        self._pending_gift_image_urls.pop(image_key, None)
        data = bytes(reply.readAll())
        reply.deleteLater()
        pixmap = QPixmap()
        if data and pixmap.loadFromData(data):
            self.gift_image_cache_dir.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(data)
            scaled = pixmap.scaled(48, 48, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            for label in labels:
                label.setText("")
                label.setPixmap(scaled)
        else:
            for label in labels:
                label.setText("无图")

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

    def _add_quick_reply(self) -> None:
        item = QListWidgetItem("新回复")
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        self.quick_replies.addItem(item)
        self.quick_replies.editItem(item)

    def _del_quick_reply(self) -> None:
        row = self.quick_replies.currentRow()
        if row >= 0:
            self.quick_replies.takeItem(row)

    def on_like_click_toggled(self, checked: bool) -> None:
        if checked:
            self.like_click_button.setText("停止连点")
            interval = self.like_interval_spin.value()
            duration = self.like_duration_spin.value()
            self._like_click_timer.setInterval(interval)
            self._like_click_timer.start()
            self._like_duration_timer.start(duration * 1000)
            self._do_like_click()
            self.like_status_label.setText(f"连点中：间隔{interval}ms，持续{duration}秒")
            self.like_status_label.setStyleSheet("color: #267a36;")
        else:
            self._stop_like_click()

    def _do_like_click(self) -> None:
        w = self.web_view.width()
        h = self.web_view.height()
        center = QPoint(w // 2, h // 2)
        QTest.mouseClick(self.web_view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center)

    def _stop_like_click(self) -> None:
        self._like_click_timer.stop()
        self._like_duration_timer.stop()
        self.like_click_button.setChecked(False)
        self.like_click_button.setText("开始连点")
        self.like_status_label.setText("连点状态：已停止")
        self.like_status_label.setStyleSheet("color: #666;")

    def _add_danmaku(self) -> None:
        item = QListWidgetItem("新弹幕")
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        self.danmaku_list.addItem(item)
        self.danmaku_list.editItem(item)

    def _del_danmaku(self) -> None:
        row = self.danmaku_list.currentRow()
        if row >= 0:
            self.danmaku_list.takeItem(row)

    def on_danmaku_toggled(self, checked: bool) -> None:
        if checked:
            count = self.danmaku_list.count()
            if count == 0:
                self.danmaku_start_button.setChecked(False)
                return
            self.danmaku_start_button.setText("停止弹幕")
            self._danmaku_index = 0
            interval = self.danmaku_interval_spin.value()
            self._danmaku_timer.setInterval(interval * 1000)
            self._danmaku_timer.start()
            self._send_danmaku()
            mode_text = "顺序" if self.danmaku_mode_combo.currentData() == "sequential" else "随机"
            self.danmaku_status_label.setText(f"弹幕发送中：{mode_text}，间隔{interval}秒")
            self.danmaku_status_label.setStyleSheet("color: #267a36;")
        else:
            self._stop_danmaku()

    def _send_danmaku(self) -> None:
        count = self.danmaku_list.count()
        if count == 0:
            self._stop_danmaku()
            return
        import random
        if self.danmaku_mode_combo.currentData() == "random":
            idx = random.randint(0, count - 1)
        else:
            idx = self._danmaku_index % count
            self._danmaku_index += 1
        item = self.danmaku_list.item(idx)
        if item and item.text():
            proposal = ActionProposal(
                event_id="manual",
                rule_id="danmaku-loop",
                rule_name="循环弹幕",
                text=item.text(),
                auto_send=False,
            )
            self._append_action(proposal)
            self._try_send(proposal)

    def _stop_danmaku(self) -> None:
        self._danmaku_timer.stop()
        self.danmaku_start_button.setChecked(False)
        self.danmaku_start_button.setText("开始弹幕")
        self.danmaku_status_label.setText("弹幕状态：已停止")
        self.danmaku_status_label.setStyleSheet("color: #666;")

    def on_transcode_toggled(self, checked: bool) -> None:
        self._log_debug(f"on_transcode_toggled checked={checked}")
        if checked:
            self.transcode_button.setText("停止转码")
            self._start_transcode()
        else:
            self.transcode_button.setText("转码画面")
            self._ffmpeg_proxy.stop()
            self._clear_overlay()
            self.statusBar().showMessage("转码已停止")

    def _start_transcode(self) -> None:
        import re
        import threading
        import urllib.request

        # Inject a JavaScript watcher that monitors for stream URLs
        # Douyin loads stream data dynamically after the page loads
        watcher_js = r'''
        (function() {
            if (window.__dmmWatcher) clearInterval(window.__dmmWatcher);
            window.__dmmStreamUrl = null;
            window.__dmmInterceptedUrls = [];

            function scoreUrl(u) {
                if (!u || u.indexOf('only_audio') !== -1) return -1;
                if (u.indexOf('_or4') !== -1) return 5;
                if (u.indexOf('_uhd') !== -1) return 4;
                if (u.indexOf('_Stage0T000hd') !== -1) return 4;
                if (u.indexOf('_Stage0T000ld') !== -1) return 3;
                if (u.indexOf('_md') !== -1) return 2;
                if (u.indexOf('_sd') !== -1) return 1;
                if (u.indexOf('_ld') !== -1) return 0;
                return 3;
            }

            function findUrls(text) {
                var matches = text.match(/https?:[^\s"']*\.flv(?:\\u0026|[^\s"'])*/g);
                if (!matches) return [];
                var urls = [];
                for (var i = 0; i < matches.length; i++) {
                    urls.push(matches[i].replace(/\\u0026/g, '&'));
                }
                return urls;
            }

            function pickBest(urls) {
                var withAuth = urls.filter(function(u) {
                    return u.indexOf('wsSecret') !== -1 || (u.indexOf('expire=') !== -1 && u.indexOf('sign=') !== -1);
                });
                var pool = withAuth.length > 0 ? withAuth : urls;
                var best = null, bestScore = -1;
                for (var i = 0; i < pool.length; i++) {
                    var s = scoreUrl(pool[i]);
                    if (s > bestScore) { bestScore = s; best = pool[i]; }
                }
                return best;
            }

            function checkUrl(url) {
                if (!url) return;
                var lower = url.toLowerCase();
                if (lower.indexOf('.flv') !== -1 || lower.indexOf('flv?') !== -1 || lower.indexOf('flv&') !== -1 ||
                    lower.indexOf('.m3u8') !== -1 || lower.indexOf('pull-hs') !== -1 || lower.indexOf('pull-hl') !== -1 ||
                    lower.indexOf('pull-c6') !== -1 || lower.indexOf('pull-c3') !== -1) {
                    window.__dmmInterceptedUrls.push(url);
                    if (!window.__dmmStreamUrl && (lower.indexOf('.flv') !== -1 || lower.indexOf('flv?') !== -1 || lower.indexOf('flv&') !== -1)) {
                        window.__dmmStreamUrl = url;
                    }
                }
            }

            // Intercept XMLHttpRequest
            var origOpen = XMLHttpRequest.prototype.open;
            var origSend = XMLHttpRequest.prototype.send;
            XMLHttpRequest.prototype.open = function(method, url) {
                this._dmmUrl = url;
                return origOpen.apply(this, arguments);
            };
            XMLHttpRequest.prototype.send = function() {
                var self = this;
                this.addEventListener('load', function() {
                    try {
                        var respUrl = self.responseURL || self._dmmUrl || '';
                        checkUrl(respUrl);
                        // Also check response text for embedded URLs
                        if (self.responseText && typeof self.responseText === 'string') {
                            var urls = findUrls(self.responseText);
                            if (urls.length > 0) {
                                var best = pickBest(urls);
                                if (best) window.__dmmStreamUrl = best;
                            }
                        }
                    } catch(e) {}
                });
                return origSend.apply(this, arguments);
            };

            // Intercept fetch
            var origFetch = window.fetch;
            window.fetch = function() {
                var url = arguments[0];
                if (typeof url === 'string') checkUrl(url);
                else if (url && url.url) checkUrl(url.url);
                return origFetch.apply(this, arguments).then(function(resp) {
                    try { checkUrl(resp.url); } catch(e) {}
                    return resp;
                });
            };

            function scanPage() {
                var scripts = document.querySelectorAll('script');
                var allUrls = [];
                for (var j = 0; j < scripts.length; j++) {
                    var txt = scripts[j].textContent || '';
                    allUrls = allUrls.concat(findUrls(txt));
                }
                var best = pickBest(allUrls);
                if (best) { window.__dmmStreamUrl = best; return true; }
                return false;
            }

            // Try webcast API
            function tryApi() {
                var xhr = new XMLHttpRequest();
                xhr.open('GET', '/webcast/room/web/enter/?aid=6383&web_rid=' + (window.location.pathname.match(/\\d+/) || [''])[0]);
                xhr.onload = function() {
                    try {
                        var data = JSON.parse(xhr.responseText);
                        var room = data.data && data.data.data && data.data.data[0];
                        if (room && room.stream_url) {
                            // Try flv_pull_url
                            if (room.stream_url.flv_pull_url) {
                                var flv = room.stream_url.flv_pull_url;
                                var keys = Object.keys(flv);
                                if (keys.length > 0) {
                                    window.__dmmStreamUrl = flv[keys[0]];
                                }
                            }
                            // Try hls_pull_url_map
                            if (room.stream_url.hls_pull_url_map) {
                                var hls = room.stream_url.hls_pull_url_map;
                                var hkeys = Object.keys(hls);
                                if (hkeys.length > 0 && !window.__dmmStreamUrl) {
                                    window.__dmmStreamUrl = hls[hkeys[0]];
                                }
                            }
                            // Try stream_url.live_core_sdk_data
                            var lcd = room.stream_url.live_core_sdk_data;
                            if (lcd && lcd.pull_data && lcd.pull_data.stream_data) {
                                try {
                                    var sd = JSON.parse(lcd.pull_data.stream_data);
                                    if (sd && sd.data) {
                                        var dk = Object.keys(sd.data);
                                        for (var i = 0; i < dk.length; i++) {
                                            var d = sd.data[dk[i]];
                                            if (d.main && d.main.flv) {
                                                window.__dmmStreamUrl = d.main.flv;
                                                break;
                                            }
                                            if (d.main && d.main.hls) {
                                                if (!window.__dmmStreamUrl) window.__dmmStreamUrl = d.main.hls;
                                            }
                                        }
                                    }
                                } catch(e2) {}
                            }
                        }
                    } catch(e) {}
                };
                xhr.send();
            }

            // Also scan for PerformanceResourceTiming entries (actual fetched URLs)
            function scanResourceTiming() {
                try {
                    var entries = performance.getEntriesByType('resource');
                    for (var i = 0; i < entries.length; i++) {
                        checkUrl(entries[i].name);
                    }
                } catch(e) {}
            }

            // Initial scan
            if (!scanPage()) {
                tryApi();
                scanResourceTiming();
                // Poll every 2s for up to 30s
                var count = 0;
                window.__dmmWatcher = setInterval(function() {
                    count++;
                    if (window.__dmmStreamUrl || count > 15) {
                        clearInterval(window.__dmmWatcher);
                        return;
                    }
                    scanResourceTiming();
                    if (!scanPage() && count % 5 === 0) {
                        tryApi();
                    }
                }, 2000);
            }
            return 'watching';
        })()
        '''

        self.web_view.page().runJavaScript(watcher_js, lambda r: QTimer.singleShot(1000, self._poll_for_stream_url))

    def _poll_for_stream_url(self) -> None:
        """Poll the injected watcher for a found stream URL."""
        def _check(url_str):
            url_str = str(url_str or "").strip()
            if url_str and url_str.startswith("http"):
                if self._ffmpeg_proxy.running:
                    self._log_debug(f"stream URL found but FFmpeg already running, skipping: {url_str[:80]}")
                else:
                    self._log_debug(f"stream URL found: {url_str[:120]}")
                    self._start_ffmpeg_with_url(url_str)
            elif self.transcode_button.isChecked():
                # Keep polling
                QTimer.singleShot(2000, self._poll_for_stream_url)
            # else: user unchecked, stop polling

        self.web_view.page().runJavaScript("window.__dmmStreamUrl || ''", _check)

    def _on_intercepted_stream_url(self, url: str) -> None:
        """Called by the network interceptor when a stream URL is detected."""
        self._log_debug(f"intercepted stream URL: {url[:150]}")
        if self.transcode_button.isChecked() and not self._ffmpeg_proxy.running:
            self._start_ffmpeg_with_url(url)

    def _start_ffmpeg_with_url(self, stream_url: str) -> None:
        self._log_debug(f"starting FFmpeg with: {stream_url[:120]}")
        # Get cookies for CDN auth
        def _got_cookies(cookie_str):
            cookies = str(cookie_str or "")
            self._log_debug(f"cookies for FFmpeg: {len(cookies)} bytes")
            if self._ffmpeg_proxy.start(stream_url, cookies=cookies):
                self._log_debug("FFmpeg started, creating overlay")
                self._sig_create_overlay.emit()
            else:
                self._log_debug("FFmpeg failed to start")
                QTimer.singleShot(0, lambda: self.transcode_button.setChecked(False))

        self.web_view.page().runJavaScript("document.cookie", _got_cookies)

    def _fetch_and_start_transcode(self, page_url: str, cookies: str) -> None:
        import re
        import threading
        import urllib.request

        self._log_debug(f"page_url={page_url}")
        self._log_debug(f"cookies len={len(cookies)}")
        m = re.search(r"live\.douyin\.com/(\d+)", page_url)
        if not m:
            self.statusBar().showMessage("请先在 live.douyin.com 打开直播间")
            self.transcode_button.setChecked(False)
            self._log_debug("no room ID in URL")
            return
        room_id = m.group(1)
        self.statusBar().showMessage(f"房间 {room_id}，正在获取流地址…")
        self._log_debug(f"room_id={room_id}")

        def _fetch():
            try:
                req = urllib.request.Request(
                    f"https://live.douyin.com/{room_id}",
                    headers={
                        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                        "Accept": "text/html,application/xhtml+xml",
                        "Accept-Encoding": "identity",
                        "Cookie": cookies,
                    },
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    html = resp.read().decode("utf-8", errors="replace")
                    self._log_debug(f"fetched HTML, len={len(html)}")
            except Exception as exc:
                self._log_debug(f"fetch error: {exc}")
                QTimer.singleShot(0, lambda: self._on_fetch_error(str(exc)))
                return

            # Prefer higher quality: uhd > or4 > md > sd > ld
            import re as _re
            best_url = None

            # Method 1: extract flv_pull_url JSON keys (most reliable)
            flv_match = _re.search(r'"flv_pull_url"\s*:\s*\{([^}]+)\}', html)
            if flv_match:
                self._log_debug(f"found flv_pull_url block")
                quality_keys = ["FULL_HD1", "HD1", "SD1", "LD1", "ORIGIN"]
                for qkey in quality_keys:
                    m_url = _re.search(rf'"{qkey}"\s*:\s*"(https?://[^"]+)"', flv_match.group(1))
                    if m_url:
                        url = m_url.group(1).replace("\\u0026", "&")
                        best_url = url
                        self._log_debug(f"flv_pull_url {qkey}: {url[:120]}")
                        break

            # Method 2: regex for FLV URLs with various quality suffixes
            if not best_url:
                quality_suffixes = ["_uhd", "_or4", "_md", "_sd", "_ld", "_ld2", "_ld4", ""]
                for suffix in quality_suffixes:
                    pattern = rf'(https?://pull-\w+[^\s"\\]+{suffix}\.flv[^\s"\\]*)'
                    matches = _re.findall(pattern, html)
                    if matches:
                        raw = matches[0]
                        url = raw.replace("\\u0026", "&")
                        best_url = url
                        self._log_debug(f"regex FLV (suffix={suffix}): {url[:120]}")
                        break

            # Method 3: fallback to any m3u8
            if not best_url:
                matches = _re.findall(r'(https?://pull-\w+[^\s"\\]+\.m3u8[^\s"\\]*)', html)
                if matches:
                    best_url = matches[0].replace("\\u0026", "&")
                    self._log_debug(f"regex M3U8: {best_url[:120]}")

            if best_url:
                self._log_debug(f"starting FFmpeg with: {best_url[:120]}")
                if self._ffmpeg_proxy.start(best_url, cookies=cookies):
                    self._log_debug("FFmpeg started, creating overlay")
                    self._sig_create_overlay.emit()
                else:
                    self._log_debug("FFmpeg failed to start")
                    QTimer.singleShot(0, lambda: self.transcode_button.setChecked(False))
            else:
                self._log_debug("no stream URL found in HTML")
                QTimer.singleShot(0, lambda: self._on_fetch_error("页面中未找到流地址"))

        threading.Thread(target=_fetch, daemon=True).start()

    def _on_fetch_error(self, msg: str) -> None:
        self.statusBar().showMessage(f"获取流地址失败: {msg}")
        self.transcode_button.setChecked(False)

    def _create_transcode_overlay(self) -> None:
        self.statusBar().showMessage("转码已启动，正在创建叠加画面…")
        self._log_debug("creating native Qt overlay")

        # Clean up previous overlay
        self._clear_overlay()

        # Create overlay inside the dedicated container layout
        from PySide6.QtWebEngineWidgets import QWebEngineView
        overlay = QWebEngineView()
        overlay.setUrl(QUrl("http://127.0.0.1:18923/player.html"))
        self._overlay_layout.addWidget(overlay)
        # Place overlay at leftmost before metadata arrives
        self._splitter.insertWidget(0, self._overlay_container)
        self._overlay_container.setVisible(True)

        # Handle close and status via title change
        def on_title_changed(title):
            if title == "DMM_CLOSE":
                self._clear_overlay()
                self._ffmpeg_proxy.stop()
                self.transcode_button.setChecked(False)
            elif title.startswith("DMM:meta "):
                try:
                    wh = title[9:].split("x")
                    w, h = int(wh[0]), int(wh[1])
                    self._overlay_aspect = w / h
                    self._apply_layout_orientation(w, h)
                    self._log_debug(f"player: {title[4:]}, aspect={self._overlay_aspect:.2f}")
                except (ValueError, IndexError):
                    self._log_debug(f"player: {title[4:]}")
            elif title.startswith("DMM:"):
                self._log_debug(f"player: {title[4:]}")

        overlay.titleChanged.connect(on_title_changed)
        self._hls_overlay = overlay
        self._log_debug("native overlay created")

    def _apply_layout_orientation(self, video_w: int, video_h: int) -> None:
        landscape = video_w > video_h

        # Rebuild web_container layout: web_view on top, event_tabs below
        old_layout = self._web_layout
        while old_layout.count():
            old_layout.takeAt(0)
        QWidget().setLayout(old_layout)

        self._web_layout = QVBoxLayout(self._web_container)
        self._web_layout.setContentsMargins(0, 0, 0, 0)
        self._web_layout.setSpacing(2)
        self._web_layout.addWidget(self.web_view, 1)
        self._web_layout.addWidget(self._event_tabs_widget, 1)

        # Move overlay_container to leftmost panel in splitter
        self._splitter.insertWidget(0, self._overlay_container)
        self._overlay_container.setVisible(True)

        # Size overlay to respect aspect ratio
        if landscape:
            self._overlay_container.setMinimumWidth(int(200 * self._overlay_aspect))
            self._overlay_container.setMaximumWidth(16777215)
        else:
            self._overlay_container.setMinimumHeight(int(200 / self._overlay_aspect))
            self._overlay_container.setMaximumHeight(16777215)

        orient = "horizontal" if landscape else "vertical"
        self._log_debug(f"layout switched to {orient} for {video_w}x{video_h}")

    def _clear_overlay(self) -> None:
        if hasattr(self, '_hls_overlay') and self._hls_overlay:
            self._overlay_layout.removeWidget(self._hls_overlay)
            self._hls_overlay.close()
            self._hls_overlay = None
        if hasattr(self, '_overlay_container'):
            self._overlay_container.setVisible(False)
            self._overlay_container.setMinimumSize(0, 0)
            self._overlay_container.setMaximumSize(16777215, 16777215)
            # Remove from splitter so it doesn't occupy space
            self._overlay_container.setParent(None)

        # Restore event_tabs to middle panel in splitter
        if hasattr(self, '_event_tabs_widget') and hasattr(self, '_splitter'):
            self._splitter.insertWidget(1, self._event_tabs_widget)

        # Restore web_container layout to just web_view
        if hasattr(self, '_web_layout'):
            old_layout = self._web_layout
            while old_layout.count():
                old_layout.takeAt(0)
            QWidget().setLayout(old_layout)
            self._web_layout = QHBoxLayout(self._web_container)
            self._web_layout.setContentsMargins(0, 0, 0, 0)
            self._web_layout.setSpacing(2)
            self._web_layout.addWidget(self.web_view, 1)

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
        self._all_events.clear()
        self.event_table.setRowCount(0)
        self.parse_debug_table.setRowCount(0)
        self.parse_debug_detail.clear()
        self.action_list.clear()
        self.statusBar().showMessage("事件和动作日志已清空")

    def _export_session_log(self) -> None:
        log_dir = Path(__file__).resolve().parents[2] / "data" / "session_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{self.session.name}_{self.session.started_at.strftime('%Y%m%d_%H%M%S')}.html"
        filepath = log_dir / filename

        events = self._all_events
        gifts = [(ts, e) for ts, e in events if e.type.value == "gift"]
        follows = [(ts, e) for ts, e in events if e.type.value == "follow"]
        chats = [(ts, e) for ts, e in events if e.type.value == "chat"]
        enters = [(ts, e) for ts, e in events if e.type.value == "user_enter"]

        def _row(ts: str, e: LiveEvent) -> str:
            color = {"gift": "#eaa825", "follow": "#267a36", "user_enter": "#888",
                     "system": "#666", "chat": "#333"}.get(e.type.value, "#333")
            icon = {"gift": "🎁", "follow": "➕", "user_enter": "🚪",
                     "system": "ℹ️", "chat": "💬"}.get(e.type.value, "•")
            return f'<tr><td class="time">{ts}</td><td style="color:{color}">{icon} {e.label}</td><td>{e.display_user}</td><td>{e.display_content}</td></tr>'

        rows_html = "\n".join(_row(ts, e) for ts, e in events)

        gift_summary = ""
        if gifts:
            gift_counter: dict[str, int] = {}
            for _, e in gifts:
                name = e.raw.get("gift_name", "未知礼物")
                count = e.raw.get("gift_count", 1)
                gift_counter[name] = gift_counter.get(name, 0) + count
            gift_summary = "<h3>🎁 礼物统计</h3><table><tr><th>礼物名</th><th>数量</th></tr>"
            for name, cnt in sorted(gift_counter.items(), key=lambda x: -x[1]):
                gift_summary += f"<tr><td>{name}</td><td>{cnt}</td></tr>"
            gift_summary += "</table>"

        html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>{self.session.name}</title>
<style>
  body {{ font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; max-width: 960px; margin: 0 auto; padding: 20px; background: #fafafa; color: #333; }}
  h1 {{ font-size: 22px; border-bottom: 2px solid #eaa825; padding-bottom: 8px; }}
  h2 {{ font-size: 17px; color: #666; }}
  .stats {{ display: flex; gap: 20px; margin: 16px 0; }}
  .stat {{ background: #fff; border: 1px solid #e0e0e0; border-radius: 8px; padding: 12px 20px; text-align: center; }}
  .stat .num {{ font-size: 28px; font-weight: 700; color: #eaa825; }}
  .stat .label {{ font-size: 13px; color: #888; }}
  table {{ width: 100%; border-collapse: collapse; margin: 12px 0; }}
  th {{ background: #f5f5f5; text-align: left; padding: 8px 10px; font-size: 13px; border-bottom: 2px solid #e0e0e0; }}
  td {{ padding: 6px 10px; border-bottom: 1px solid #eee; font-size: 14px; }}
  td.time {{ color: #999; font-size: 12px; white-space: nowrap; }}
  tr:hover {{ background: #fffbe6; }}
</style></head><body>
<h1>{self.session.name}</h1>
<p>开始时间：{self.session.started_at.strftime("%Y-%m-%d %H:%M:%S")} &nbsp; 结束时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
<div class="stats">
  <div class="stat"><div class="num">{len(events)}</div><div class="label">总事件</div></div>
  <div class="stat"><div class="num">{len(chats)}</div><div class="label">弹幕</div></div>
  <div class="stat"><div class="num">{len(gifts)}</div><div class="label">礼物</div></div>
  <div class="stat"><div class="num">{len(enters)}</div><div class="label">进场</div></div>
  <div class="stat"><div class="num">{len(follows)}</div><div class="label">关注</div></div>
</div>
{gift_summary}
<h2>📋 全部事件</h2>
<table><tr><th>时间</th><th>类型</th><th>用户</th><th>内容</th></tr>
{rows_html}
</table>
</body></html>"""

        filepath.write_text(html, encoding="utf-8")
        self.statusBar().showMessage(f"场次日志已导出：{filepath.name}")

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.mock_source.stop()
        self.web_source.stop()
        self.login_status_timer.stop()
        self._like_click_timer.stop()
        self._like_duration_timer.stop()
        self._danmaku_timer.stop()
        self._ffmpeg_proxy.stop()
        self.web_view.setUrl(QUrl("about:blank"))
        self.session.end()
        self.database.save_session(self.session)
        if self._all_events:
            self._export_session_log()
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
