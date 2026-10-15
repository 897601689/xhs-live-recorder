"""主窗口：状态栏、关注列表、运行日志与托盘"""

import os
import threading
import time
from collections import OrderedDict
from xhs import api
from xhs import config
from collections import deque
from xhs import profiles
from xhs import recorder
from xhs import settings
from xhs import state
from xhs import utils
from PySide6.QtCore import QEvent, QRect, QSize, QTimer, QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton, QSystemTrayIcon, QTextEdit, QVBoxLayout, QWidget
from xhs.ui import app_icon, chrome, following, preview_window, settings_window, theme, threads, widgets


LOG_BUFFER = 500     # 日志折叠期间最多留多少条（超出只丢最旧的，避免无限吃内存）
LOG_PANEL_LINES = 300   # 日志面板里最多显示的行数
QUIT_DRAIN_SECONDS = 15   # 退出时最多等各录制任务封箱（mkv 缺索引会不可播，不能直接杀）

class MainWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("小红书直播录制器")
        startup = self._clamped_startup_size()
        self.setMinimumSize(QSize(min(1100, startup.width() + 100),
                                  min(720, startup.height() - 20)))
        self.resize(startup)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)

        self._bridge = threads.UiBridge()
        self._bridge.task_progress.connect(self._note_progress)
        self._bridge.task_removed.connect(self._remove_task)
        self._bridge.task_finished.connect(self._finish_task)
        self._bridge.task_started.connect(self._on_task_started)
        self._bridge.following_updated.connect(self._on_following_loaded)
        self._bridge.logged.connect(self._on_core_log)
        self._bridge.auth_state.connect(self._on_auth_state)

        # 每秒一条的进度不直接刷界面：先攒着，400ms 合并一次
        self._pending_progress = OrderedDict()
        self._task_bytes = {}             # key -> 最新一条进度的原始字节数（用于合计）
        self._progress_timer = QTimer(self)
        self._progress_timer.setInterval(400)
        self._progress_timer.timeout.connect(self._flush_progress)

        self._task_cards = OrderedDict()   # key -> 昵称，仅用于统计与关注列表状态同步
        self._completed_count = 0
        self._monitor_thread = None
        self._monitor_stopping = False    # 点了「停止监控」，但这一轮轮询还没跑完
        self._following_thread = None
        self._sid_thread = None
        self._following_signature = None
        self._force_quit = False        # True 时关闭窗口即真正退出，否则最小化到托盘
        self._quit_confirmed = False    # 退出确认只做一次，避免托盘「退出」被问两遍
        self._settings_open = False     # 设置窗是用户主动打开的（用于主窗体恢复时是否一并还原）
        self._pending_logs = deque(maxlen=LOG_BUFFER)   # 日志面板展开前先缓冲，展开时一次补齐
        self._log_alerts = 0            # 折叠期间收到的警告/错误条数
        self._auth_expired = False      # 标题栏红灯已知的登录态，只在上升沿弹一次托盘
        self.tray = None

        backdrop = chrome.Backdrop()
        self.setCentralWidget(backdrop)
        root = QVBoxLayout(backdrop)
        self._root_layout = root
        root.setContentsMargins(*self._content_margins())
        root.setSpacing(12)

        self.title_bar = chrome.TitleBar()
        self.title_bar.settings_clicked.connect(self._open_settings)
        root.addWidget(self.title_bar)

        # 设置界面独立成窗口，主界面只保留关注列表
        self.settings_window = settings_window.SettingsWindow(self)

        body = QHBoxLayout()
        body.setSpacing(14)
        body.addWidget(self._build_following_panel(), 1)
        root.addLayout(body, 1)
        root.addWidget(self._build_log_drawer())

        state.set_ui_listener(self._on_core_event)
        self._update_counters()
        # 程序启动即读取整份关注列表（含未开播用户）
        QTimer.singleShot(0, lambda: self._load_following(verbose=True))
        # 启动即进入监控：发现已开播且「自动录制」为开的用户会直接开录
        QTimer.singleShot(0, self._auto_start_monitor)

        if QSystemTrayIcon.isSystemTrayAvailable():
            self._init_tray()

    # ---------- 窗口尺寸 ----------
    def _clamped_startup_size(self) -> QSize:
        """默认尺寸按屏幕可用区域收敛

        原来固定 1280×840 / 最小 1100×720：1366×768 的笔记本减掉任务栏只剩约 730 高，
        窗口底部（分页按钮、录制合计）会伸到屏幕外面，鼠标点不到。
        """
        screen = QApplication.primaryScreen()
        area = screen.availableGeometry() if screen is not None else QRect(0, 0, 1280, 900)
        return QSize(min(1280, max(880, area.width() - 48)),
                     min(840, max(520, area.height() - 48)))

    def _content_margins(self) -> tuple:
        """最大化时收紧四周留白

        那 14px 是给窗口投影留的；铺满屏幕时投影没人看见，留下只是白吃内容区，
        还会让底边框（连同合计行）顶到屏幕边缘。
        """
        return (6, 6, 6, 6) if self.isMaximized() else (14, 14, 14, 14)

    # ---------- 系统托盘 ----------
    def _init_tray(self) -> None:
        """系统托盘图标：单击恢复窗口，右键菜单提供「显示窗体 / 退出」"""
        self.tray = QSystemTrayIcon(self)
        self.tray.setToolTip("小红书直播录制器")
        self.tray.setIcon(app_icon.icon())      # 图标不跟主题走，建一次就够

        menu = widgets.RoundedMenu(self)      # 圆角卡片：普通 QMenu 整窗不透明，四个角是实心的
        show_action = menu.addAction("显示窗体")
        show_action.triggered.connect(self._restore_from_tray)
        log_action = menu.addAction("打开日志文件")
        log_action.triggered.connect(self._open_log_file)
        menu.addSeparator()
        quit_action = menu.addAction("退出")
        quit_action.triggered.connect(self._quit_from_tray)
        self.tray.setContextMenu(menu)

        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _open_log_file(self) -> None:
        """托盘直接开日志：录制出问题时，第一个要问的就是「昨晚到底怎么了」"""
        path = state.log_file_path()
        if not os.path.exists(path):
            self._append_log(f"还没有日志文件：{path}", "warn")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _on_tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self._restore_from_tray()

    def _sync_settings_with_window(self, visible: bool) -> None:
        """设置窗跟随主窗体收放：主窗最小化 / 收进托盘时一起藏，恢复时按原样还原"""
        panel = getattr(self, "settings_window", None)
        if panel is None:
            return
        if visible:
            if self._settings_open:
                panel.show()
                panel.raise_()          # 兜底：主流桌面环境下 Z 序已由父子关系保证
        else:
            # 用 hide 而非 close：保留了设置窗里没点 ✓ 的临时输入，也不会误清还原标记
            panel.hide()

    def changeEvent(self, event) -> None:
        # 最小化 / 还原（含从任务栏点回来）都要把设置窗一起带走
        if event.type() == QEvent.Type.WindowStateChange:
            self._sync_settings_with_window(not self.isMinimized())
            maximized = self.isMaximized()
            if maximized:
                self._fit_work_area()
            self.title_bar.set_maximized(maximized)
            self._root_layout.setContentsMargins(*self._content_margins())
        super().changeEvent(event)

    def _fit_work_area(self) -> None:
        """无边框窗体最大化时按整块屏幕算，会盖住任务栏：收回到可用区域

        盖住任务栏后任务栏点不到，底部一行内容也看不见，是最大化最容易踩的坑。
        """
        screen = (QApplication.screenAt(self.frameGeometry().center())
                  or QApplication.primaryScreen())
        if screen is None:
            return
        target = screen.availableGeometry()
        if self.geometry() != target:   # 已就位就不再动，避免布局回环
            self.setGeometry(target)

    def _restore_from_tray(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        if self.isMinimized():
            self.showNormal()
        self._sync_settings_with_window(True)   # 收进托盘前开着的话一并还原

    def _minimize_to_tray(self) -> None:
        self.hide()
        self._sync_settings_with_window(False)   # 用 hide 而非 close，保留设置窗里未提交的输入
        if self.tray is not None and self.tray.isVisible():
            self.tray.showMessage("小红书直播录制器",
                                  "已最小化到托盘，点击图标或右键「显示窗体」可恢复",
                                  QSystemTrayIcon.MessageIcon.Information, 2500)

    def _quit_from_tray(self) -> None:
        self._force_quit = True
        self.close()
        # closeEvent 里如果用户在确认框上选了「不退」，会把 _force_quit 退回 False，
        # 那时进程必须留下（窗体可能本来就藏在托盘里，不能靠 isVisible 判断）
        if not self._force_quit:
            return
        if self.tray is not None:
            self.tray.hide()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    # ---------- 界面构建 ----------
    def _build_following_panel(self) -> QFrame:
        """关注列表：工具栏 + 卡片/列表双显示"""
        card = QFrame()
        card.setObjectName("glassCard")
        # 这里不加 QGraphicsDropShadowEffect：列表每次刷新都有控件增删，
        # 阴影效果会在每次重绘时对整块内容做 pixmap + 高斯模糊，拖窗口时明显卡顿。
        # 静态的设置窗底纹仍然保留阴影。
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(8)
        title = QLabel("关注列表")
        title.setObjectName("sectionTitle")
        self.following_state = QLabel("")
        self.following_state.setObjectName("appSubTitle")
        head.addWidget(title)
        head.addWidget(self.following_state)
        head.addStretch()
        head.addWidget(self._build_log_toggle())
        layout.addLayout(head)

        self.following_view = following.FollowingView()
        self.following_view.action.connect(self._on_following_action)
        layout.addWidget(self.following_view, 1)
        return card

    def _build_log_toggle(self) -> QPushButton:
        """日志抽屉开关：默认折叠，出错时按钮上直接带出条数"""
        button = QPushButton("运行日志")
        button.setObjectName("pageBtn")
        button.setCheckable(True)
        button.setFixedHeight(24)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip("展开查看运行日志（开播、断流、报错都记在这里）")
        button.toggled.connect(self._toggle_log_panel)
        self.log_toggle = button
        return button

    def _build_log_drawer(self) -> QWidget:
        """运行日志面板

        打包成窗口程序后没有任何地方能看到 emit_log 的输出：录制出了问题
        （取不到流、ffmpeg 崩了、sid 过期）用户完全无从判断，只能干等。
        这里给一个默认折叠的抽屉，错误还能在托盘气泡里提示一句。
        """
        self.log_view = QTextEdit()
        self.log_view.setObjectName("logView")
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.log_view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.log_view.setFixedHeight(150)
        self.log_view.setVisible(False)
        self.log_view.setFrameShape(QFrame.Shape.NoFrame)
        # 长时间运行的录制器不该把日志一直堆在内存里：超过上限由 Qt 自动丢最旧的行
        self.log_view.document().setMaximumBlockCount(LOG_PANEL_LINES)
        return self.log_view

    def _toggle_log_panel(self, visible: bool) -> None:
        self.log_view.setVisible(visible)
        if not visible:
            return
        self._flush_pending_logs()
        self._log_alerts = 0
        self._set_log_toggle_text()

    def _set_log_toggle_text(self) -> None:
        if self._log_alerts and not self.log_view.isVisible():
            self.log_toggle.setText(f"运行日志 · {self._log_alerts} 条提醒")
            self.log_toggle.setToolTip("有警告或错误级日志，点击展开查看")
        else:
            self.log_toggle.setText("运行日志")
            self.log_toggle.setToolTip("展开查看运行日志（开播、断流、报错都记在这里）")

    def _on_core_log(self, message: str, level: str) -> None:
        """核心层日志落到界面抽屉（由信号桥保证在主线程执行）"""
        line = f"[{utils.current_time_formatted()}] {message}"
        if self.log_view.isVisible():
            self.log_view.append(line)
            self.log_view.verticalScrollBar().setValue(
                self.log_view.verticalScrollBar().maximum())
        else:
            # 折叠时只缓冲，不驱动界面重排
            self._pending_logs.append(line)
        if level in ("error", "warn"):
            self._log_alerts += 1
            self._set_log_toggle_text()
        if level == "error":
            self._notify_tray("录制器遇到问题", message)

    def _flush_pending_logs(self) -> None:
        """展开时补齐折叠期间漏掉的行"""
        if not self._pending_logs:
            return
        for line in self._pending_logs:
            self.log_view.append(line)
        self._pending_logs.clear()
        self.log_view.verticalScrollBar().setValue(
            self.log_view.verticalScrollBar().maximum())

    # ---------- 核心事件（工作线程 -> 信号 -> 主线程） ----------
    def _on_core_event(self, event: str, payload: dict) -> None:
        if event == "progress":
            self._bridge.task_progress.emit(
                payload["key"], payload.get("user_id", ""), payload["nickname"],
                payload["size"], payload["speed"], payload["elapsed"],
                int(payload.get("bytes") or 0))
        elif event == "removed":
            self._bridge.task_removed.emit(payload["key"], payload.get("user_id", ""))
        elif event == "finished":
            self._bridge.task_finished.emit(
                payload["key"], payload.get("user_id", ""), payload["nickname"],
                payload["size"], payload["duration"], payload["avg_speed"])
        elif event == "started":
            self._bridge.task_started.emit(
                payload.get("key", ""), payload.get("user_id", ""), payload.get("nickname", ""))
        elif event == "following":
            self._bridge.following_updated.emit(
                payload.get("users") or [], payload.get("verbose", False))
        elif event == "log":
            self._bridge.logged.emit(payload.get("message", ""), payload.get("level", "info"))
        elif event == "auth":
            self._bridge.auth_state.emit(bool(payload.get("expired")),
                                         payload.get("message", ""))

    def _on_auth_state(self, expired: bool, message: str) -> None:
        """登录态失效：胶囊常驻红灯 + 托盘只提醒一次

        轮询每 30 秒就会重复上报一次失效，所以托盘按上升沿走；胶囊是常驻的，
        不需要每条都动它。
        """
        if self._auth_expired == expired:
            return
        self._auth_expired = expired
        self.title_bar.set_auth_expired(expired)
        if expired:
            self._notify_tray(
                "登录态已失效",
                f"{message}\n打开「设置 → 登录态」重新粘贴 sid。正在录制的几路不受影响。")

    # ---------- 录制任务状态 ----------
    def _note_progress(self, key: str, user_id: str, nickname: str,
                       size: str, speed: str, elapsed: str, raw_bytes: int = 0) -> None:
        """攒下最新一条进度：同一个 key 后到的覆盖先到的，400ms 合并刷一次界面

        每个任务每秒上报一条，五六个在录就是每秒五六次整表重排；
        人眼本来也看不出 250ms 内的差别，合并后主线程负载只剩四分之一。
        """
        self._pending_progress[key] = (user_id, nickname, size, speed, elapsed, raw_bytes)
        self._task_bytes[key] = raw_bytes
        if not self._progress_timer.isActive():
            self._progress_timer.start()

    def _flush_progress(self) -> None:
        if not self._pending_progress:
            self._progress_timer.stop()
            return
        pending = list(self._pending_progress.items())
        self._pending_progress.clear()
        for key, (user_id, nickname, size, speed, elapsed, _bytes) in pending:
            self._update_task(key, user_id, nickname, size, speed, elapsed)
        self._update_counters()

    def _update_task(self, key: str, user_id: str, nickname: str,
                     size: str, speed: str, elapsed: str) -> None:
        user_id = user_id or str(key).split("_", 1)[0]
        if self._task_cards.get(key) is None:
            self._task_cards[key] = nickname
            # 关注列表同步进入「录制中」状态
            self.following_view.set_last_record(user_id, profiles.mark_last_record(user_id, nickname))
        self.following_view.update_record(user_id, size, speed, elapsed)

    def _remove_task(self, key: str, user_id: str = "") -> None:
        # 即便任务没来得及上报进度（启动失败）也要清掉「录制中」标记，
        # 否则列表里的按钮会一直停在「停止录制」
        self._task_cards.pop(key, None)
        self._pending_progress.pop(key, None)   # 已结束的进度不能再刷回界面
        self._task_bytes.pop(key, None)
        self.following_view.clear_record(user_id or str(key).split("_", 1)[0])
        self._update_counters()

    def _finish_task(self, key: str, user_id: str, nickname: str, size: str,
                     duration: str, avg_speed: str) -> None:
        self._completed_count += 1
        user_id = user_id or str(key).split("_", 1)[0]
        self._remove_task(key, user_id)
        self.following_view.set_last_record(user_id, profiles.mark_last_record(user_id, nickname))
        self._append_log(f"[{nickname}] 录制完成，共 {size}，用时 {duration}，平均 {avg_speed}", "success")
        self._notify_tray("录制完成", f"{nickname}｜{size}，用时 {duration}")

    def _on_task_started(self, key: str, user_id: str, nickname: str) -> None:
        """自动录制开始：界面在托盘后面时也要能知道"""
        if key:
            self._task_cards.setdefault(key, nickname)
            self.following_view.set_last_record(user_id, profiles.mark_last_record(user_id, nickname))
            self._update_counters()
        self._notify_tray("已开始录制", f"{nickname} 开播了，正在自动录制")

    def _notify_tray(self, title: str, message: str) -> None:
        if self.tray is None or not self.tray.isVisible():
            return
        self.tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 4000)

    def _append_log(self, message: str, level: str = "info") -> None:
        """界面侧提示统一进日志抽屉（顺带到终端，方便开发时对照）"""
        mark = "!" if level == "error" else ("*" if level == "warn" else " ")
        try:
            print(f"[{utils.current_time_formatted()}]{mark} {message}")
        except (OSError, ValueError):
            pass
        self._on_core_log(message, level)

    # ---------- 交互 ----------
    def _open_settings(self) -> None:
        self._settings_open = True
        self.settings_window.place_center()    # 内部会同步宽高，再按主窗体中心定位
        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        panel = getattr(self, "settings_window", None)
        if panel is not None:
            panel.sync_width()
            panel.sync_height()

    def _switch_theme(self, name: str) -> None:
        """切换主题：立即生效并写入界面设置，下次启动沿用"""
        if name not in config.THEME_NAMES:
            return
        if name == settings.current_theme():
            self.settings_window._sync_theme_buttons()
            return
        settings.set_theme(name)
        self._apply_theme()
        self._append_log(f"外观已切换为「{config.THEME_NAMES[name]}」主题，下次启动保持该设置")

    def _save_poll_interval(self, _value: int = 0) -> None:
        """设置界面改动轮询间隔时立即写盘（SpinBox 的 valueChanged 会带上新值）"""
        settings.set_poll_interval(self.min_spin.value(), self.max_spin.value())

    def _confirm_name_template(self) -> None:
        """校验并保存文件命名规则（设置窗 ✓ 按钮）"""
        template = self.settings_window.name_edit.text()
        ok, message = settings.validate_name_template(template)
        if not ok:
            self._append_log(f"命名规则未保存：{message}", "error")
            return
        settings.set_name_template(template)
        self._append_log(f"文件命名规则已保存，下次录制生效: {template.strip()}")

    def _read_sid_input(self) -> str:
        """读取输入框内容：允许直接粘整段抓包文本，这里统一抠出真正的 sid"""
        window = self.settings_window
        sid = settings.extract_sid(window.sid_edit.text())
        if sid != window.sid_edit.text():
            window.sid_edit.setText(sid)   # 回写提纯后的值，输入框里所见即实际生效值
        return sid

    def _save_sid(self) -> None:
        """保存登录态 sid（接口唯一校验的凭证），保存后后续请求立即生效"""
        window = self.settings_window
        sid = self._read_sid_input()
        if not sid:
            window.sid_status.setText("sid 不能为空，未保存")
            return
        settings.set_sid(sid)
        window.sid_status.setText("已保存，后续请求立即生效")
        self._append_log("登录态 sid 已更新")

    def _test_sid(self) -> None:
        """用输入框里的 sid 打一次关注列表，验证是否仍然有效"""
        window = self.settings_window
        if self._sid_thread is not None and self._sid_thread.isRunning():
            return
        sid = self._read_sid_input()
        if not sid:
            window.sid_status.setText("请先填写 sid")
            return
        settings.set_sid(sid)                      # 测试也按输入框里的值走，所见即所得
        window.sid_status.setText("测试中…")
        window.sid_test.setEnabled(False)
        self._sid_thread = threads.SidTestThread(self)
        self._sid_thread.finished_with.connect(self._on_sid_tested)
        self._sid_thread.start()

    def _on_sid_tested(self, ok: bool, message: str) -> None:
        """登录态测试结果回写设置界面"""
        window = self.settings_window
        window.sid_test.setEnabled(True)
        window.sid_status.setText(("✓ " if ok else "✕ ") + message)
        self._append_log(f"登录态测试: {message}", "info" if ok else "error")
        self._sid_thread = None

    def _apply_theme(self) -> None:
        """把当前主题套到全界面：样式表 + 自绘部件（窗口底纹 / 卡片 / 日志）"""
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(theme.theme_qss())
        self.title_bar.refresh_theme()
        self.settings_window.refresh_theme()
        self.following_view.refresh_theme()   # 只重刷行/卡里手写的胶囊配色，不重建整页
        preview_window.refresh_all()          # 卡片底色是 paintEvent 现场画的，不走 Backdrop，只能叫它重画
        for host in (self, self.settings_window):
            for backdrop in host.findChildren(chrome.Backdrop):
                backdrop.refresh_theme()

    def _auto_start_monitor(self) -> None:
        """程序启动自动进入监控（可在设置里停止）"""
        if config.FFMPEG_PATH is None:
            self._append_log("未找到 ffmpeg，已跳过自动启动监控", "warn")
            return
        self._start_monitor()

    def _toggle_monitor(self) -> None:
        if self._monitor_thread is not None and self._monitor_thread.isRunning():
            self._stop_monitor()
        else:
            self._start_monitor()

    def _start_monitor(self) -> None:
        if config.FFMPEG_PATH is None:
            self._append_log("未找到 ffmpeg，无法录制，请先安装并加入 PATH", "error")
            return
        self._monitor_thread = threads.MonitorThread(self.min_spin.value(), self.max_spin.value(), self)
        self._monitor_thread.finished.connect(self._on_monitor_stopped)
        self._monitor_thread.start()
        self.monitor_btn.setText("停止监控")
        self._apply_button_style(self.monitor_btn, "stopBtn")
        self._monitor_stopping = False
        self._update_counters()

    def _stop_monitor(self) -> None:
        if self._monitor_thread is None:
            return
        self._monitor_thread.request_stop()
        self._monitor_stopping = True
        self.monitor_btn.setEnabled(False)
        self._update_counters()
        self._append_log("正在停止轮询，等待本轮结束…（已在录制的任务会继续到直播结束）", "warn")

    @staticmethod
    def _apply_button_style(button: QPushButton, object_name: str) -> None:
        button.setObjectName(object_name)
        button.style().unpolish(button)
        button.style().polish(button)

    def _on_monitor_stopped(self) -> None:
        self.monitor_btn.setText("开始监控")
        self.monitor_btn.setEnabled(True)
        self._apply_button_style(self.monitor_btn, "primaryBtn")
        self._monitor_thread = None
        self._monitor_stopping = False
        self._update_counters()      # 胶囊跟着回到「未启动」
        self._append_log("监控系统已停止", "warn")

    def _browse_output(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择录制文件保存目录", settings.output_root())
        if not path:
            return
        settings.set_output_dir(path)
        self.out_edit.setText(path)
        self.out_edit.setToolTip(path)
        self._append_log(f"输出目录已切换到: {path}，下次启动保持")

    def _open_folder(self, user: dict) -> None:
        """打开该用户的录制目录（按当前命名规则推导），不存在时回退到输出根目录"""
        user = user or {}
        rel_dir, _file_base = settings.render_name_template(settings.current_name_template(), user)
        nickname = utils.sanitize_filename(str(user.get("nickname") or ""))
        root_dir = settings.output_root()
        candidates = []
        if rel_dir:
            candidates.append(os.path.join(root_dir, rel_dir))
        if nickname:
            # 兼容旧目录：早期版本以 xhs/昵称 或直接昵称命名
            candidates.append(os.path.join(root_dir, config.LEGACY_PLATFORM_DIR, nickname))
            candidates.append(os.path.join(root_dir, nickname))
        candidates.append(root_dir)
        for path in candidates:
            if os.path.isdir(path):
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))
                return
        os.makedirs(root_dir, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(root_dir))

    # ---------- 关注列表 ----------
    def _load_following(self, verbose: bool = False) -> None:
        """读取整份关注列表（程序启动时自动调用，或点击「刷新」触发）"""
        if self._following_thread is not None and self._following_thread.isRunning():
            if verbose:
                self._append_log("关注列表正在刷新中，请稍候", "warn")
            return
        self.following_state.setText("加载中…")
        # 列表区自己也得改口成「正在读取」：首屏那一页空壳上面挂着这句，
        # 手动刷新时（手上还留着上一批数据）它不动，不会把列表闪成空页
        self.following_view.set_loading(True)
        thread = threads.FollowingLoadThread(self)
        thread.loaded.connect(lambda users: self._on_following_loaded(users, True))
        thread.failed.connect(self._on_following_failed)
        thread.finished.connect(self._on_following_thread_done)
        self._following_thread = thread
        thread.start()

    def _on_following_thread_done(self) -> None:
        self._following_thread = None

    def _on_following_loaded(self, users: list, verbose: bool = True) -> None:
        self.following_state.setText("")
        self.following_view.set_loading(False)
        users = users or []
        signature = tuple((u.get("user_id"), u.get("is_live"), u.get("room_id"),
                           u.get("remark", ""), u.get("last_record", "")) for u in users)
        fresh = signature != self._following_signature
        self._following_signature = signature
        if not fresh:
            self._update_auto_record_stat()
            return  # 数据无变化：不重建，避免滚动位置与交互被打断
        # 轮询刷新保持当前页码（有人上下播就跳回第一页，正在翻页的人会被打断），
        # 只有首次加载与手动点「刷新」才回到第一页
        self.following_view.set_users(users, reset_page=verbose)
        self._update_auto_record_stat()
        if verbose:
            live = sum(1 for u in users if u.get("is_live"))
            self._append_log(f"关注列表已加载：{len(users)} 位，其中 {live} 位正在直播")

    def _on_following_failed(self, reason: str) -> None:
        self.following_state.setText("")
        self.following_view.set_loading(False)
        self._append_log(f"获取关注列表失败：{reason}", "error")

    def _on_following_action(self, action: str, user: dict) -> None:
        if action == "refresh":
            self._load_following(verbose=True)
        elif action == "record":
            self._start_user_record(user)
        elif action == "stop":
            self._stop_user_record(user)
        elif action == "open":
            self._open_folder(user)
        elif action == "copy":
            room_id = str(user.get("room_id") or "")
            if room_id:
                QApplication.clipboard().setText(room_id)
                self._append_log(f"[{user.get('nickname')}] 房间号已复制: {room_id}")
            else:
                self._append_log("该用户当前没有房间号", "warn")
        elif action == "preview":
            # 起进程是即时返回的，不必开线程；返回的那句话本身就是给用户的说明
            self._append_log(preview_window.open_card(user, self))
        elif action == "preview_stop":
            nickname = user.get("nickname", "")
            if preview_window.close_card(user.get("user_id")):
                self._append_log(f"[{nickname}] 预览已关闭")
            else:
                # 窗口多半是用户自己点的叉，或断流后 -autoexit 收的
                self._append_log(f"[{nickname}] 预览窗口已经关闭")
        elif action == "auto":
            self._toggle_auto_record(user, bool(user.get("auto_record", True)))

    def _start_user_record(self, user: dict) -> None:
        nickname = user.get("nickname", "")
        user_id = user.get("user_id", "")
        if config.FFMPEG_PATH is None:
            self.following_view.clear_pending(user_id)
            self._append_log("未找到 ffmpeg，无法录制，请先安装并加入 PATH", "error")
            return
        if not user.get("is_live"):
            self.following_view.clear_pending(user_id)
            self._append_log(f"[{nickname}] 当前未开播，无法录制", "warn")
            return
        if not user.get("video_url"):
            self.following_view.clear_pending(user_id)
            self._append_log(f"[{nickname}] 未取到直播流地址，请刷新后再试", "warn")
            return
        threading.Thread(target=api.check_and_launch_download,
                         args=(api.build_download_info(user),), daemon=True).start()
        self._append_log(f"[{nickname}] 已手动开始录制")

    def _stop_user_record(self, user: dict) -> None:
        nickname = user.get("nickname", "")
        key = f"{user.get('user_id')}_{user.get('room_id')}"
        if recorder.request_stop_download(key):
            self._append_log(f"[{nickname}] 正在停止录制，文件会正常保存…", "warn")
        else:
            self.following_view.clear_pending(user.get("user_id", ""))
            self._append_log(f"[{nickname}] 当前没有进行中的录制任务", "warn")

    def _toggle_auto_record(self, user: dict, enabled: bool) -> None:
        """切换某位用户是否参与自动录制（原黑名单功能的替代）"""
        nickname = user.get("nickname", "")
        profiles.set_auto_record(user.get("user_id", ""), nickname, enabled)
        self.following_view.set_auto_record(user.get("user_id", ""), enabled)
        self._update_auto_record_stat()
        self._append_log(f"[{nickname}] 已{'开启' if enabled else '关闭'}自动录制")

    def _update_auto_record_stat(self) -> None:
        users = self.following_view.users()
        if not users:
            self.auto_count_label.setText("关注列表加载后显示开启数量")
            return
        enabled = sum(1 for user in users if user.get("auto_record"))
        self.auto_count_label.setText(f"已开启 {enabled} / {len(users)} 位")

    def _update_counters(self) -> None:
        running = len(self._task_cards)
        self.stat_label.setText(f"录制中 {running} · 已完成 {self._completed_count}")
        self.following_view.refresh_totals(self._totals_text(running))
        # 标题栏的胶囊跟着这里一起更新：主窗被别的窗盖住时，那是唯一还在说话的地方
        thread = self._monitor_thread
        self.title_bar.set_status(thread is not None and thread.isRunning(),
                                  self._monitor_stopping, running)

    def _totals_text(self, running: int) -> str:
        parts = []
        if running:
            parts.append(f"已录 {utils.format_file_size(sum(self._task_bytes.values()))}")
        free = state.free_disk_bytes(settings.output_root())
        if free:
            parts.append(f"磁盘剩余 {utils.format_file_size(free)}")
            if free < config.DISK_MIN_FREE_START:
                parts.append("不足，暂不开新录")
        return " · ".join(parts)

    def closeEvent(self, event) -> None:
        # 点关闭按钮只是藏到托盘（后台继续监控录制），真正退出走托盘菜单的「退出」
        if not self._force_quit and self.tray is not None and self.tray.isVisible():
            event.ignore()
            self._minimize_to_tray()
            return
        if not self._prepare_exit():
            self._force_quit = False
            event.ignore()
            return
        self.settings_window.close()
        self._stop_monitor()
        state.set_ui_listener(None)
        thread = self._following_thread   # 等关注列表请求收尾，避免线程未结束即销毁
        if thread is not None and thread.isRunning():
            thread.wait(1500)
        profiles.flush_user_profiles()             # 把延迟落盘的用户档案写干净
        preview_window.close_all()   # 预览卡片是我们开的，退出后还留在桌面上就成了孤儿
        super().closeEvent(event)

    def _prepare_exit(self) -> bool:
        """退出前的收尾：录制中先问一句，同意后停轮询、把各段文件正常封箱

        直接退会让 ffmpeg 收不到 EOF，写出的 mkv 缺索引基本不可播，
        对录制器来说等于把这半场直播弄丢了。
        """
        self._progress_timer.stop()
        with state.active_downloads_lock:
            running = len(state.active_downloads)
        if running and not self._quit_confirmed:
            if not self.isVisible():
                self._restore_from_tray()   # 确认框要有个可见的父窗体才看得到
            if not widgets.confirm(
                    self, "还有录制在进行",
                    f"当前有 {running} 场直播正在录制。\n退出前会先请求它们停止并正常保存"
                    f"（每场最多等 {QUIT_DRAIN_SECONDS} 秒），确定退出吗？",
                    "退出并保存", "继续录制"):
                return False
            self._quit_confirmed = True
        if running:
            self._stop_monitor_thread()   # 先停轮询，免得收尾途中又开一场新录制
            self._drain_active_recordings()
        return True

    def _stop_monitor_thread(self) -> None:
        thread = self._monitor_thread
        if thread is None or not thread.isRunning():
            return
        thread.request_stop()
        thread.wait(4000)   # 阻塞主线程：退出流程里界面已经没有交互意义

    def _drain_active_recordings(self) -> None:
        with state.active_downloads_lock:
            keys = list(state.active_downloads)
        for key in keys:
            recorder.request_stop_download(key)
        if not keys:
            return
        self._append_log("正在等待录制任务保存文件…", "warn")
        deadline = time.time() + QUIT_DRAIN_SECONDS
        left = keys
        while time.time() < deadline:
            with state.active_downloads_lock:
                left = [key for key in state.active_downloads if key in keys]
            if not left:
                return
            QApplication.processEvents()
            time.sleep(0.2)
        self._append_log(f"仍有 {len(left)} 场未完成封箱，"
                         f"文件可能只能播放到中断点", "warn")
