"""设置窗口：外观、轮询、输出目录、命名规则、登录态"""

from xhs import config
from xhs import settings
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget
from xhs.ui import chrome, theme


class SettingsWindow(QWidget):
    """设置界面（独立窗口，主界面不再展示）：监控开关 / 轮询间隔 / 输出目录 / 自动录制统计"""

    WIDTH_RATIO = 18 / 25       # 主窗体 : 设置窗 = 25 : 18（主窗宽度 1280 时设置窗 922）
    MIN_WIDTH = 380             # 主窗体被拖得很窄时的下限
    MIN_HEIGHT = 520            # 窗口高度下限（内容不足时不出滚动条）
    HEIGHT_RATIO = 0.92         # 最高不超过主窗体高度的 92%
    # 设置窗是浮在主窗体之上的顶层窗，发丝描边压不住背景，加深描边并加投影把轮廓撑出来
    BORDER_ALPHA = 96
    SHADOW_MARGIN = 18          # 给投影留出的外边距，避免阴影被窗口边界裁掉

    def __init__(self, owner):
        # 把 owner 当 parent 传进去：配合 Qt.Window 标志成为「主窗体的附属顶层窗」，
        # 由窗口管理器保证它永远压在主窗体之上（不必再手动 raise_），且不再单独占任务栏
        super().__init__(owner)
        self.owner = owner
        self.setWindowTitle("设置")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.sync_width()
        self.sync_height()
        self._build_ui()

    def place_center(self) -> None:
        """摆到主窗体正中心（跟随主窗体位置，不是屏幕中心）

        尺寸先同步再算坐标，否则按旧尺寸居中会偏；最后夹在屏幕可用区内，
        避免主窗体贴边时设置窗被任务栏或屏幕边缘切掉。
        """
        owner = getattr(self, "owner", None)
        self.sync_width()
        self.sync_height()
        if owner is None:
            return
        x = owner.x() + (owner.width() - self.width()) // 2
        y = owner.y() + (owner.height() - self.height()) // 2
        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            x = max(area.x(), min(x, area.right() - self.width() + 1))
            y = max(area.y(), min(y, area.bottom() - self.height() + 1))
        self.move(x, y)

    def closeEvent(self, event) -> None:
        """用户主动关闭才会走到这里（被主窗体连带 hide 不会触发），据此清掉还原标记"""
        owner = getattr(self, "owner", None)
        if owner is not None:
            setattr(owner, "_settings_open", False)
        super().closeEvent(event)

    def keyPressEvent(self, event) -> None:
        """Esc 关闭：模态窗口约定，输入控件里的 Esc 仍优先按控件自身行为处理"""
        if event.key() == Qt.Key.Key_Escape:
            event.accept()
            self.close()
            return
        super().keyPressEvent(event)

    def sync_width(self) -> None:
        """宽度跟随主窗体：主窗 : 设置窗 = 25 : 18（主窗宽度 1280 时设置窗 922）"""
        owner_width = self.owner.width() if self.owner is not None else 1280
        width = max(self.MIN_WIDTH, int(owner_width * self.WIDTH_RATIO))
        if self.width() != width:
            self.setFixedWidth(width)

    def sync_height(self) -> None:
        """限高：不超过主窗高度 92%，也不超出屏幕可用区域；装不下的内容交给内部滚动"""
        owner_height = self.owner.height() if self.owner is not None else 840
        limit = int(owner_height * self.HEIGHT_RATIO)
        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None:
            limit = min(limit, screen.availableGeometry().height() - 60)
        limit = max(self.MIN_HEIGHT, limit)
        self.setMinimumHeight(min(self.MIN_HEIGHT, limit))
        self.setMaximumHeight(limit)
        if self.height() > limit:
            self.resize(self.width(), limit)

    def _build_ui(self) -> None:
        """构建设置窗体内容（只在初始化时执行一次，避免重复布局）"""
        backdrop = chrome.Backdrop(border_alpha=self.BORDER_ALPHA)
        wrap = QVBoxLayout(self)
        wrap.setContentsMargins(self.SHADOW_MARGIN, self.SHADOW_MARGIN,
                                self.SHADOW_MARGIN, self.SHADOW_MARGIN)
        wrap.addWidget(backdrop)
        shadow = QGraphicsDropShadowEffect(backdrop)
        shadow.setBlurRadius(32)
        shadow.setOffset(0, 6)
        # 投影固定用黑色：两套主题下都是暗影，浅色主题压得住、深色主题不糊边
        shadow.setColor(QColor(0, 0, 0, 120))
        backdrop.setGraphicsEffect(shadow)

        layout = QVBoxLayout(backdrop)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(8)
        title = QLabel("设置")
        title.setObjectName("sectionTitle")
        close_btn = QPushButton(theme.CLOSE_GLYPH)
        close_btn.setObjectName("iconClose")
        close_btn.setFixedSize(34, 30)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setToolTip("关闭设置")
        close_btn.clicked.connect(self.close)
        head.addWidget(title)
        head.addStretch()
        head.addWidget(close_btn)
        layout.addLayout(head)

        # 其余设置项装进滚动容器：窗口限高后，装不下的内容可滚动查看
        content = QWidget()
        body = QVBoxLayout(content)
        # 右侧留出空隙：8px 滚动条浮在内容上，不垫开会压到「浏览 / ✓ / 测试」这类贴边按钮
        body.setContentsMargins(0, 0, 14, 0)
        body.setSpacing(10)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}"
                             "QScrollArea>QWidget>QWidget{background:transparent;}")
        layout.addWidget(scroll, 1)
        layout = body   # 后续代码照旧往 layout 里加控件，实际都进了滚动内容区

        self.monitor_btn = QPushButton("开始监控")
        self.monitor_btn.setObjectName("primaryBtn")
        self.monitor_btn.setFixedHeight(44)
        self.monitor_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.monitor_btn.setToolTip("轮询关注列表，发现有人开播自动录制")
        self.monitor_btn.clicked.connect(self.owner._toggle_monitor)
        layout.addWidget(self.monitor_btn)

        poll_row = QHBoxLayout()
        poll_row.setSpacing(8)
        poll_caption = QLabel("轮询间隔")
        poll_caption.setObjectName("fieldLabel")
        self.min_spin = QSpinBox()
        self.max_spin = QSpinBox()
        for spin, value in zip((self.min_spin, self.max_spin), settings.current_poll_interval()):
            spin.setRange(config.POLL_MIN_LIMIT, config.POLL_MAX_LIMIT)
            spin.setValue(value)      # 初值取当前生效的轮询间隔，否则 QSpinBox 会停在下限
            spin.setSuffix(" 秒")
            spin.setFixedHeight(36)
            spin.setMinimumWidth(86)
        tilde = QLabel("~")
        tilde.setObjectName("fieldLabel")
        poll_row.addWidget(poll_caption)
        poll_row.addWidget(self.min_spin)
        poll_row.addWidget(tilde)
        poll_row.addWidget(self.max_spin)
        poll_row.addStretch()
        layout.addLayout(poll_row)

        out_caption = QLabel("输出目录")
        out_caption.setObjectName("fieldLabel")
        layout.addWidget(out_caption)

        out_row = QHBoxLayout()
        out_row.setSpacing(8)
        self.out_edit = QLineEdit(settings.output_root())
        self.out_edit.setReadOnly(True)
        self.out_edit.setFixedHeight(36)
        self.out_edit.setToolTip(settings.output_root())
        browse = QPushButton("浏览")
        browse.setObjectName("ghostBtn")
        browse.setFixedHeight(36)
        browse.setCursor(Qt.CursorShape.PointingHandCursor)
        browse.clicked.connect(self.owner._browse_output)
        out_row.addWidget(self.out_edit, 1)
        out_row.addWidget(browse)
        layout.addLayout(out_row)

        # ---------- 文件命名规则 ----------
        name_caption_row = QHBoxLayout()
        name_caption = QLabel("文件命名规则")
        name_caption.setObjectName("fieldLabel")
        name_help = QLabel("?")
        name_help.setObjectName("fieldLabel")
        name_help.setToolTip(
            "可用标签：{platform} {channelId} {remarks} {owner} {title}\n"
            "{year} {month} {date} {hour} {min} {sec} {ms}\n"
            "用 / 分隔可建立子目录，点击下方标签可插入到光标处，改完点 ✓ 保存")
        name_help.setCursor(Qt.CursorShape.WhatsThisCursor)
        name_caption_row.addWidget(name_caption)
        name_caption_row.addWidget(name_help)
        name_caption_row.addStretch()
        layout.addLayout(name_caption_row)

        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        self.name_edit = QLineEdit(settings.current_name_template())
        self.name_edit.setFixedHeight(36)
        self.name_edit.setToolTip(name_help.toolTip())
        name_ok = QPushButton("✓")
        name_ok.setObjectName("iconBtn")
        name_ok.setFixedHeight(36)
        name_ok.setFixedWidth(36)
        name_ok.setCursor(Qt.CursorShape.PointingHandCursor)
        name_ok.setToolTip("校验并保存命名规则")
        name_ok.clicked.connect(self.owner._confirm_name_template)
        name_row.addWidget(self.name_edit, 1)
        name_row.addWidget(name_ok)
        layout.addLayout(name_row)

        # 可点击的标签：点击插入到输入框光标处（单行排布，放得下就不换行）
        tag_row = QHBoxLayout()
        tag_row.setSpacing(6)
        for tag in [f"{{{tag}}}" for tag in settings.NAME_TEMPLATE_FIELDS]:
            chip = QPushButton(tag)
            chip.setObjectName("tagChip")
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setToolTip(f"插入 {tag}")
            chip.clicked.connect(lambda checked=False, text=tag: self.name_edit.insert(text))
            tag_row.addWidget(chip)
        tag_row.addStretch(1)
        layout.addLayout(tag_row)

        ffmpeg_row = QHBoxLayout()
        ffmpeg_caption = QLabel("FFmpeg")
        ffmpeg_caption.setObjectName("fieldLabel")
        ffmpeg_value = QLabel("已就绪" if config.FFMPEG_PATH else "未找到")
        ffmpeg_value.setObjectName("fieldValue")
        self.ffmpeg_dot = QLabel()
        self.ffmpeg_dot.setFixedSize(8, 8)
        self._paint_ffmpeg_dot()
        ffmpeg_row.addWidget(ffmpeg_caption)
        ffmpeg_row.addStretch()
        ffmpeg_row.addWidget(self.ffmpeg_dot)
        ffmpeg_row.addWidget(ffmpeg_value)
        layout.addLayout(ffmpeg_row)

        layout.addWidget(theme._divider())
        sid_caption = QLabel("登录态")
        sid_caption.setObjectName("sectionTitle")
        layout.addWidget(sid_caption)

        sid_hint = QLabel("接口只校验 sid（会话 ID）。失效后从手机抓包取新的 sid 粘到这里即可，"
                          "也可以直接粘贴整段 xy-common-params / 请求 URL，程序会自动抠出 sid；"
                          "等同于账号密码，请勿外传。")
        sid_hint.setObjectName("fieldLabel")
        sid_hint.setWordWrap(True)
        layout.addWidget(sid_hint)

        sid_row = QHBoxLayout()
        sid_row.setSpacing(8)
        self.sid_edit = QLineEdit(settings.current_sid())
        self.sid_edit.setFixedHeight(36)
        self.sid_edit.setEchoMode(QLineEdit.EchoMode.Password)   # 凭证默认不显示明文
        self.sid_edit.setToolTip("登录态凭证 sid，可在链接里直接抓到；保存后立即对后续请求生效")
        self.sid_save = QPushButton("✓")
        self.sid_save.setObjectName("iconBtn")
        self.sid_save.setFixedSize(36, 36)
        self.sid_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sid_save.setToolTip("保存 sid（写入 ui_settings.json，下次启动保持）")
        self.sid_save.clicked.connect(self.owner._save_sid)
        self.sid_test = QPushButton("测试")
        self.sid_test.setObjectName("ghostBtn")
        self.sid_test.setFixedHeight(36)
        self.sid_test.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sid_test.setToolTip("用当前 sid 请求一次关注列表")
        self.sid_test.clicked.connect(self.owner._test_sid)
        sid_row.addWidget(self.sid_edit, 1)
        sid_row.addWidget(self.sid_save)
        sid_row.addWidget(self.sid_test)
        layout.addLayout(sid_row)

        self.sid_status = QLabel("—")
        self.sid_status.setObjectName("fieldLabel")
        layout.addWidget(self.sid_status)

        layout.addWidget(theme._divider())
        theme_caption = QLabel("外观")
        theme_caption.setObjectName("sectionTitle")
        layout.addWidget(theme_caption)

        theme_row = QHBoxLayout()
        theme_row.setSpacing(8)
        self.theme_buttons = {}
        for name, text in config.THEME_NAMES.items():
            button = QPushButton(text)
            button.setObjectName("viewToggle")
            button.setCheckable(True)
            button.setFixedHeight(30)
            button.setMinimumWidth(72)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(f"切换到{text}主题，下次启动保持")
            button.clicked.connect(lambda checked=False, n=name: self.owner._switch_theme(n))
            self.theme_buttons[name] = button
            theme_row.addWidget(button)
        theme_row.addStretch()
        layout.addLayout(theme_row)
        self._sync_theme_buttons()

        layout.addWidget(theme._divider())
        auto_caption = QLabel("自动录制")
        auto_caption.setObjectName("sectionTitle")
        layout.addWidget(auto_caption)

        auto_hint = QLabel("监控开启后，仅「自动录制」为开的用户会在开播时自动开录；"
                           "关闭的用户仍可手动录制。可在关注列表卡片（或「···」菜单）上随时切换。")
        auto_hint.setObjectName("fieldLabel")
        auto_hint.setWordWrap(True)
        layout.addWidget(auto_hint)

        self.auto_count_label = QLabel("—")
        self.auto_count_label.setObjectName("fieldValue")
        layout.addWidget(self.auto_count_label)
        layout.addStretch()

        layout.addWidget(theme._divider())
        self.stat_label = QLabel()
        self.stat_label.setObjectName("fieldLabel")
        layout.addWidget(self.stat_label)

        # 主窗口的启停监控 / 目录切换 / 统计刷新仍按这些属性名访问控件
        self.owner.monitor_btn = self.monitor_btn
        self.owner.min_spin = self.min_spin
        self.owner.max_spin = self.max_spin
        self.owner.out_edit = self.out_edit
        self.owner.auto_count_label = self.auto_count_label
        self.owner.stat_label = self.stat_label

        # 轮询间隔改动即写入 ui_settings.json（放在属性挂载之后，避免初始化时误触发）
        for spin in (self.min_spin, self.max_spin):
            spin.valueChanged.connect(self.owner._save_poll_interval)

    def _paint_ffmpeg_dot(self) -> None:
        key = "ok" if config.FFMPEG_PATH else "err"
        self.ffmpeg_dot.setStyleSheet(f"background:{theme._rgb(key)};border-radius:4px;")

    def _sync_theme_buttons(self) -> None:
        active = settings.current_theme()
        for name, button in self.theme_buttons.items():
            button.setChecked(name == active)

    def refresh_theme(self) -> None:
        """切换主题后重绘手写配色的控件（FFmpeg 圆点 / 主题按钮选中态）
        样式表统一由 app 级 theme_qss() 负责，这里不再重复设置"""
        self._paint_ffmpeg_dot()
        self._sync_theme_buttons()

    def mousePressEvent(self, event) -> None:
        handle = self.windowHandle()
        if handle is not None and event.button() == Qt.MouseButton.LeftButton:
            handle.startSystemMove()
            return
        super().mousePressEvent(event)
