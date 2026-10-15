"""关注列表面板：分页、筛选、卡片 / 列表两种模式"""

from xhs import config
from xhs import preview
from xhs import settings
from PySide6.QtCore import QEvent, QTimer, Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QApplication, QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget, QWidgetAction
from xhs.ui import entries, theme, widgets


class FollowingView(QWidget):
    """关注列表视图：工具栏筛选 + 卡片/列表双显示 + 分页"""

    action = Signal(str, dict)   # record / stop / open / auto / copy / refresh
    PAGE_SIZES = config.PAGE_SIZE_CHOICES
    # 每页数量照参考样式写成「20条/页」：真实值走 itemData，落盘和分页都读它
    PAGE_SIZE_LABELS = tuple("全部" if size == config.PAGE_SIZE_ALL else f"{size}条/页"
                             for size in PAGE_SIZES)
    # 排序：工具栏下拉与表头箭头共用一份状态，SORTS[i] 对应 SORT_KEYS[i]
    SORTS = ("默认排序", "主播名", "直播状态", "录制状态", "最近录制时间")
    SORT_KEYS = ("default", "user", "live", "record", "last")
    # 每个字段的首次方向：名字正序更合直觉，状态与时间都是「有 / 新的在前」
    NATURAL_DESC = {"user": False, "live": True, "record": True, "last": True}
    SORTABLE = ("user", "live", "record", "last")   # 可点表头排序的列；房间号 / 平台 / 操作不算
    # 筛选拆成三组：直播、录制、监听各一个下拉。
    # 合成一个下拉时互斥，恰好选不出「正在直播但没在录」这种最该被看到的一档。
    # 筛选下拉里只放实打实的条件，「全部」不占一项：空值 = 不筛，靠悬浮 × 退回空值
    LIVE_FILTERS = ("正在直播", "未开播")
    RECORD_FILTERS = ("录制中", "未录制")
    MONITOR_FILTERS = ("自动", "手动")
    # 平台是唯一的例外：它原来只是摆着看的「小红书」，没人会去点，框里总得有个说得出口
    # 的默认值，就让它把「不筛」写在脸上，而不是再藏一层空值。
    ALL_PLATFORMS = "全部平台"
    MODES = (("card", "卡片"), ("list", "列表"))
    # 卡片视图固定每行 CARD_COLUMNS 张，剩余宽度平摊给每张卡片，使整行刚好铺满
    CARD_SPACING = 10
    CARD_COLUMNS = 5       # 每行固定 5 张卡片
    CARD_MIN_WIDTH = 186
    CARD_MAX_WIDTH = 320
    # 「正在读取关注列表」那三枚点：一步 500ms，三拍一轮（亮 1 枚 → 2 枚 → 3 枚）。
    # 慢到不抢注意力，而且整页只动这一行字
    HINT_TICK = 500
    HINT_DOTS = 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self._users = []
        self._records = {}
        self._filtered_total = 0   # 上一次渲染时「筛选后的条数」，底部那行要用
        self._widgets = {}
        self._pool = {}       # user_id -> 上一轮摘下来待复用的控件（差量重建用）
        self._signature = None   # 当前控件的布局特征，与结果不符时整池作废
        self._list_header = None
        self._list_heads = {}    # 表头各列的标签：key → QLabel（宽度就地跟随列宽）
        self._head_captions = {}  # 表头原标题（不含排序箭头）
        self._col_widths = None  # 上一次分配出的列宽，没变就不必再刷一遍
        self._pendings = {}   # user_id -> 点击后的乐观按钮状态，重建后回填
        self._mode = settings.current_view_mode()   # 沿用上次选择的卡片 / 列表显示模式
        self._page = 0
        self._loading = True
        self._card_width = entries.FollowingCard.WIDTH

        self._visible_key = None   # 上一次真正渲染出来的（页码 + user_id 序列），一致就就地刷新
        self._page_items = []      # 当页用户：主播名列宽按这其中最长的昵称实测
        self._sort_desc = True     # 当前排序方向，换排序字段时回到该字段的自然方向

        # 窗口拖动时用定时器收敛重建次数，避免每帧都重建卡片
        self._width_timer = QTimer(self)
        self._width_timer.setSingleShot(True)
        self._width_timer.setInterval(140)
        self._width_timer.timeout.connect(self._on_width_timer)

        # 「正在读取」那行字的三枚点依次点亮。定时器随「空状态页在显示 + 还在读 + 看得见」
        # 起停（与封面那枚录制扩散圈同一套规矩），不转的时候一律停掉，别空绘一张看不见的页
        self._hint_step = 1
        self._hint_timer = QTimer(self)
        self._hint_timer.setInterval(self.HINT_TICK)
        self._hint_timer.timeout.connect(self._advance_hint)

        # 搜索防抖：每敲一个字符就重建整页，中文输入法组词期间更是连发
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(180)
        self._filter_timer.timeout.connect(self._on_filter_changed)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        root.addLayout(self._build_toolbar())

        self.card_container = widgets.FlowContainer(margin=0, spacing=self.CARD_SPACING)
        self.card_layout = self.card_container.flow
        self.list_container = QWidget()
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(5)
        self.list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.empty_label = QLabel(self._hint_text(1))
        self.empty_label.setObjectName("emptyHint")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_clear = QPushButton("清除筛选条件")
        self.empty_refresh = QPushButton("重新读取关注列表")
        for button in (self.empty_clear, self.empty_refresh):
            button.setObjectName("ghostBtn")
            button.setFixedHeight(28)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        # 只在「筛没了」的时候露出清除按钮，纯空列表时只留刷新
        self.empty_clear.clicked.connect(self.clear_filters)
        self.empty_refresh.clicked.connect(lambda: self.action.emit("refresh", {}))
        # 两枚都先收着：首屏就是「正在读取」那一档，谁都不该露（露出来的一律由 _rebuild 决定）
        self.empty_clear.setVisible(False)
        self.empty_refresh.setVisible(False)
        empty_page = QWidget()
        empty_box = QVBoxLayout(empty_page)
        empty_box.setContentsMargins(0, 0, 0, 0)
        empty_box.setSpacing(12)
        empty_box.addStretch(1)
        empty_box.addWidget(self.empty_label)
        hint_row = QHBoxLayout()
        hint_row.setSpacing(8)
        hint_row.addStretch(1)
        hint_row.addWidget(self.empty_clear)
        hint_row.addWidget(self.empty_refresh)
        hint_row.addStretch(1)
        empty_box.addLayout(hint_row)
        empty_box.addStretch(2)

        self.stack = QStackedWidget()
        self.card_scroll = self._wrap_scroll(self.card_container)
        self.list_scroll = self._wrap_scroll(self.list_container)
        self.stack.addWidget(self.card_scroll)     # 0 卡片视图
        self.stack.addWidget(self.list_scroll)     # 1 列表视图
        self.stack.addWidget(empty_page)           # 2 空状态
        # 首屏就站在这页上：一份数据都还没读到时，卡片页和列表页都是空壳，整个窗口
        # 看着「啥都没有」，唯一的解释写在折叠着的日志抽屉里。以前只有切一次视图
        # （走 _rebuild）才会把空状态顶出来，而默认模式是列表 —— 于是列表模式必空。
        self.stack.setCurrentIndex(2)
        for scroll in (self.card_scroll, self.list_scroll):
            scroll.installEventFilter(self)   # 切页后由滚动区的 show/resize 触发列宽重算
        root.addWidget(self.stack, 1)
        root.addLayout(self._build_pager())

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)   # ←/→ 翻页要用键盘
        widgets.get_image_cache().updated.connect(self._on_image_updated)

    # ---------- 构建 ----------
    def _build_toolbar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(7)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索备注 / 房间号 / 昵称（Ctrl+F）")
        self.search_edit.setFixedHeight(28)
        self.search_edit.setMinimumWidth(140)
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._filter_timer.start)   # 防抖后再重建
        self.search_edit.setToolTip("输入即筛选；Esc 清空")
        row.addWidget(self.search_edit, 1)

        self.platform_combo = self._combo((self.ALL_PLATFORMS, config.PLATFORM_NAME), 0, 62)
        self.live_combo = self._combo(self.LIVE_FILTERS, -1, 84, "直播状态")
        self.record_combo = self._combo(self.RECORD_FILTERS, -1, 84, "录制状态")
        self.monitor_combo = self._combo(self.MONITOR_FILTERS, -1, 84, "监听状态")
        self.size_combo = self._combo(self.PAGE_SIZES, self._saved_size_index(), 72,
                                      labels=self.PAGE_SIZE_LABELS)
        self.sort_combo = self._combo(self.SORTS, 0, 90)
        self.platform_combo.setToolTip("按来源平台筛选；「全部平台」就是全量，不用清")
        self.live_combo.setToolTip("只看正在直播或未开播的关注用户")
        self.record_combo.setToolTip("只看录制中或空闲的关注用户")
        self.monitor_combo.setToolTip("只看自动监听（开播即录）或手动录制的关注用户")
        self.sort_combo.setToolTip("排序方式；列表模式下也可直接点表头切换")
        # 平台：第 0 项就是「不筛」，所以它不进下面那批悬浮 × 的筛选下拉
        self.platform_combo.currentIndexChanged.connect(self._on_filter_changed)
        row.addWidget(self.platform_combo)
        for combo in (self.live_combo, self.record_combo, self.monitor_combo):
            combo.set_clearable(True)
            combo.currentIndexChanged.connect(self._on_filter_changed)
            row.addWidget(combo)
        # 排序换字段时方向回到该字段的自然方向，沿用上次点出来的反向会让人困惑
        self.sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        row.addWidget(self.sort_combo)
        # 每页数量单独处理：选择变化即落盘，下次启动沿用
        self.size_combo.currentIndexChanged.connect(self._on_page_size_changed)
        row.addWidget(self.size_combo)

        # 卡片 / 列表：两个互斥按钮合成一个下拉，工具栏少占一位，长相也跟别的下拉统一
        self.mode_combo = self._combo(tuple(text for _, text in self.MODES), self._mode_index(), 62)
        self.mode_combo.setToolTip("切换卡片 / 列表显示；选择会记住，下次启动沿用")
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        row.addWidget(self.mode_combo)

        self.fields_btn = QPushButton("显示字段")
        self.fields_btn.setObjectName("ghostBtn")
        self.fields_btn.setFixedHeight(28)
        self.fields_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.fields_btn.setToolTip("选择列表视图显示哪些列（仅对列表模式生效）")
        self.fields_btn.clicked.connect(self._show_fields_menu)
        row.addWidget(self.fields_btn)

        refresh = QPushButton("刷新")
        refresh.setObjectName("ghostBtn")
        refresh.setFixedHeight(28)
        refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh.setToolTip("重新读取关注列表")
        refresh.clicked.connect(lambda: self.action.emit("refresh", {}))
        row.addWidget(refresh)
        return row

    def _show_fields_menu(self) -> None:
        """「显示字段」多选勾选面板

        用「QMenu + 内嵌复选框面板」而不是普通菜单项：普通菜单点一下就收起，
        一次只能改一个字段；这里复选框接管点击，菜单保持打开，可以连续勾选多个，
        每次勾选变化即时落盘并重建列表。
        """
        visible = set(settings.current_list_fields())
        panel = QFrame()
        panel.setObjectName("fieldsPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)
        boxes = {}
        for key, caption in config.LIST_FIELDS:
            box = QCheckBox(caption)
            box.setChecked(key in visible)
            box.setCursor(Qt.CursorShape.PointingHandCursor)
            boxes[key] = box
            layout.addWidget(box)

        def apply(changed_key: str) -> None:
            picked = [key for key, _caption in config.LIST_FIELDS if boxes[key].isChecked()]
            # 主列一个不剩会让整排只剩头像，至少保留一列：拒绝这次取消
            if not any(key in picked for key, _caption, _width in entries.FollowingRow.COLUMNS):
                boxes[changed_key].setChecked(True)
                return
            if set(picked) == visible:
                return
            visible.clear()
            visible.update(picked)
            settings.set_list_fields(picked)
            self._rebuild()

        for key, box in boxes.items():
            box.toggled.connect(lambda _checked, k=key: apply(k))

        menu = widgets.RoundedMenu(self)
        holder = QWidgetAction(menu)
        holder.setDefaultWidget(panel)
        menu.addAction(holder)
        menu.exec(self.fields_btn.mapToGlobal(self.fields_btn.rect().bottomLeft()))

    def _build_pager(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        self.count_label = QLabel("—")
        self.count_label.setObjectName("appSubTitle")
        # 录制中合计 / 磁盘剩余：录制器最该盯的两个数，放底部常驻而不是藏进设置页
        self.total_label = QLabel("")
        self.total_label.setObjectName("appSubTitle")
        self.prev_btn = QPushButton("上一页")
        self.next_btn = QPushButton("下一页")
        for button in (self.prev_btn, self.next_btn):
            button.setObjectName("pageBtn")
            button.setFixedHeight(26)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.prev_btn.clicked.connect(lambda: self._turn_page(-1))
        self.next_btn.clicked.connect(lambda: self._turn_page(1))
        self.page_label = QLabel("1/1")
        self.page_label.setObjectName("appSubTitle")
        row.addWidget(self.count_label)
        row.addWidget(self.total_label)
        row.addStretch()
        row.addWidget(self.prev_btn)
        row.addWidget(self.page_label)
        row.addWidget(self.next_btn)
        return row

    @staticmethod
    def _combo(items, index: int, width: int, placeholder: str = "",
               labels=()) -> widgets.ArrowComboBox:
        combo = widgets.ArrowComboBox()
        for row, item in enumerate(items):
            # 文案与取值分开存：框里写「20条/页」，代码只认 itemData 里那个 "20"
            combo.addItem(labels[row] if labels else item, item)
        combo.setCurrentIndex(index)
        if placeholder:
            combo.setPlaceholderText(placeholder)   # 空值时框内显示灰色字段名
        combo.setFixedHeight(28)
        combo.setMinimumWidth(max(width, combo.contents_fit_width()))
        combo.setCursor(Qt.CursorShape.PointingHandCursor)
        return combo

    @staticmethod
    def _wrap_scroll(widget: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(widget)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        return scroll

    def _sync_card_width(self) -> bool:
        """按可视宽度算出「刚好铺满一行」的卡片宽度；发生变化时返回 True

        小于 3px 的变化一律忽略：换主题时 QSS 重新 polish，滚动条 sizeHint 会抖 1px，
        而卡片宽度计入布局签名，抖一下就是整池卡片销毁重建（头像、布局全部重来）。
        """
        # 量 stack 而不是 card_scroll：QStackedWidget 只给当前页尺寸，没切出来的那页
        # 一直是 QWidget 的默认宽 —— 首屏停在空状态页时按卡片页自己的宽度算，
        # 会先得到一个假宽度、切出来再算一次，卡片池白白销毁重建一遍。
        area = self.stack.width()
        if area <= 0:
            return False
        scrollbar = self.card_scroll.verticalScrollBar().sizeHint().width()
        available = area - scrollbar - 2
        if available < self.CARD_MIN_WIDTH:
            available = max(1, area)
        columns = self.CARD_COLUMNS
        width = (available - self.CARD_SPACING * (columns - 1)) // columns
        width = max(1, min(self.CARD_MAX_WIDTH, width))
        if abs(width - self._card_width) < 3:
            return False
        self._card_width = width
        return True

    def _sync_list_widths(self) -> None:
        """按内容定列宽：主播名按当页最长昵称、房间号按当页最长房间号实测，其余列取下限宽

        富余宽度不再发给任何列，而是留在列与列之间的弹性间距里均分，
        整行读起来间距均匀；行与表头共用同一份宽度。
        """
        if self._mode != "list":
            return
        viewport = self.list_scroll.viewport().width()
        # 列表页还没切出来（stack 里不是当前页）时拿到的宽度是旧的：这次不算，
        # 等切页后 list_scroll 的 show / resize 事件（见 eventFilter）再算
        if viewport <= 0 or not self.list_scroll.isVisible():
            return
        fields = set(settings.current_list_fields())
        widths = {"user": self._measured_user_width()}
        for key, _caption, width in entries.FollowingRow.COLUMNS:
            if key in fields:
                if key == "last":
                    widths[key] = entries.FollowingRow.last_col_width()
                elif key == "room":
                    widths[key] = entries.FollowingRow.room_col_width(
                        str(user.get("room_id") or "") for user in self._page_items)
                else:
                    widths[key] = width
        signature = tuple(widths.get(key) for key in
                          ("user", "platform", "room", "last", "action"))
        if signature == self._col_widths:
            return   # 宽度没变，避免反复触发布局
        self._col_widths = signature
        entries.FollowingRow.USER_WIDTH = widths["user"]
        if widths.get("room"):
            entries.FollowingRow.ROOM_WIDTH = widths["room"]
        self._apply_header_widths()
        for widget in self._widgets.values():
            widget.apply_widths()

    def _measured_user_width(self) -> int:
        """主播名列宽 = 头像 32 + 间距 8 + 当页最长昵称 + 一点余量，上下限夹住"""
        row = next(iter(self._widgets.values()), None)
        head = self._list_heads.get("user")
        font = row.nick.font() if row is not None else (
            head.font() if head is not None else QApplication.font())
        metrics = QFontMetrics(font)
        text_w = max((metrics.horizontalAdvance(str(user.get("nickname") or ""))
                      for user in self._page_items), default=60)
        width = 32 + 8 + text_w + 10
        return max(entries.FollowingRow.MIN_USER_WIDTH,
                   min(width, entries.FollowingRow.MAX_USER_WIDTH))

    def _apply_header_widths(self) -> None:
        """表头各列跟随当前列宽（表头不重建，直接就地改宽）"""
        widths = {"user": entries.FollowingRow.USER_WIDTH}
        widths.update({key: width for key, _caption, width in entries.FollowingRow.columns()})
        for key, label in self._list_heads.items():
            if key in widths and label.minimumWidth() != widths[key]:
                label.setFixedWidth(widths[key])

    def _on_width_timer(self) -> None:
        if self._mode == "card":
            if self._sync_card_width():
                self._rebuild()
        else:
            self._sync_list_widths()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._width_timer.start()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._width_timer.start()
        self._sync_hint_timer()   # 从托盘 / 别的页回来时接着转

    def hideEvent(self, event) -> None:
        self._hint_timer.stop()   # 看不见就别空绘，回来由 showEvent 接上
        super().hideEvent(event)

    def eventFilter(self, watched, event) -> bool:
        """滚动区拿到真实宽度时再算一次列宽

        卡片 / 列表页在 stack 里，只有当前页会被布局；切页时 FollowingView 本身尺寸没变
        （收不到 resizeEvent，showEvent 也早就发过了），只有滚动区自己会收到 show / resize。
        不拦这两个事件的话，程序刚打开、列表页第一次切出来时量到的还是旧宽度，
        列宽会一直停在最小值直到用户手动拖窗口。
        """
        if watched in (self.card_scroll, self.list_scroll) and event.type() in (
                QEvent.Type.Show, QEvent.Type.Resize, QEvent.Type.ShowToParent):
            self._width_timer.start()
        return super().eventFilter(watched, event)

    # ---------- 数据 ----------
    def users(self) -> list:
        """当前关注列表（供主窗口统计自动录制开关）"""
        return self._users

    def set_auto_record(self, user_id: str, enabled: bool) -> None:
        """主窗口确认后，把某个用户的自动录制开关同步到界面

        卡片和列表行都要回写：列表行没有专门的开关控件，状态全靠行内的用户数据
        （「···」菜单里的「关闭 / 开启自动录制」就是按它显示当前状态的），
        漏掉行的话只能等下一轮轮询重建整页才会刷新，看起来就像点了没反应。
        """
        for user in self._users:
            if user.get("user_id") == user_id:
                user["auto_record"] = bool(enabled)
                break
        widget = self._widgets.get(user_id)
        if widget is not None:
            widget.set_auto_record(enabled)

    def set_users(self, users, reset_page: bool = True) -> None:
        self._users = [dict(user) for user in (users or [])]
        self._loading = False
        if reset_page:
            self._page = 0
        self._rebuild()

    def set_loading(self, loading: bool) -> None:
        # 手上没人时整页交给 _rebuild 去写：它同时管着那两枚按钮该不该露出来。
        # 有人时不打扰列表 —— 刷新时把已有数据闪成空页更难看。
        self._loading = loading
        if not self._users:
            self._rebuild()

    # ---------- 「正在读取」那行字的省略号 ----------
    def _hint_text(self, step: int) -> str:
        """「正在读取关注列表」后面三枚点，前 step 枚亮、其余压暗

        三枚常驻、只换颜色不换字数：字数一变，居中的那一行每拍都会左右晃着重新居中。
        """
        lit, dim = theme._rgba("text", 0.62), theme._rgba("text", 0.14)
        dots = "".join(f'<span style="color:{lit if index < step else dim}">·</span>'
                       for index in range(self.HINT_DOTS))
        return f"正在读取关注列表{dots}"

    def _hint_shown(self) -> bool:
        """这拍动画该不该跑：空状态页正显示 + 还在读 + 这一页真看得见"""
        return (self.stack.currentIndex() == 2 and self._loading
                and not self._users and self.isVisible())

    def _sync_hint_timer(self) -> None:
        if self._hint_shown():
            if not self._hint_timer.isActive():
                self._hint_step = 1
                self._hint_timer.start()
        else:
            self._hint_timer.stop()

    def _advance_hint(self) -> None:
        # 祖先被隐藏时 Qt 不会给子控件发 hideEvent（切走视图、缩进托盘都是这种），
        # 所以每一拍自己验一次可见性，发现看不见就顺手停掉
        if not self._hint_shown():
            self._hint_timer.stop()
            return
        self._hint_step = self._hint_step % self.HINT_DOTS + 1
        self.empty_label.setText(self._hint_text(self._hint_step))

    def remove_user(self, user_id: str) -> None:
        self._users = [user for user in self._users if user.get("user_id") != user_id]
        self._records.pop(user_id, None)
        self._pendings.pop(user_id, None)
        self._rebuild()

    def update_record(self, user_id: str, size: str, speed: str, elapsed: str) -> None:
        self._records[user_id] = {
            "size": size, "speed": speed,
            "elapsed": (elapsed or "").split("(")[0],
        }
        self._pendings.pop(user_id, None)   # 真实进度到了，乐观状态作废
        widget = self._widgets.get(user_id)
        if widget is not None:
            widget.set_record(self._records[user_id])
        self._refresh_summary()

    def clear_record(self, user_id: str) -> None:
        self._records.pop(user_id, None)
        self._pendings.pop(user_id, None)
        widget = self._widgets.get(user_id)
        if widget is not None:
            widget.set_record(None)
        self._refresh_summary()

    def set_last_record(self, user_id: str, text: str) -> None:
        for user in self._users:
            if user.get("user_id") == user_id:
                user["last_record"] = text
                break
        widget = self._widgets.get(user_id)
        if widget is not None:
            widget.set_last_record(text)

    def refresh_theme(self) -> None:
        """换主题后就地重画行 / 卡里的手写配色

        这些胶囊是构造时把颜色解析成字符串写进 setStyleSheet 的，QSS 换肤管不到它们。
        以前靠整页 _rebuild 顺手重做，代价是几百个控件连同头像位图全部重来；
        现在控件复用得好好保住，改成只重刷配色。
        """
        for widget in self._widgets.values():
            widget.refresh_theme()
        if self._hint_shown():
            # 省略号那三枚的颜色是当场解析成字符串写进文字的，跟胶囊一样不吃 QSS 换肤
            self.empty_label.setText(self._hint_text(self._hint_step))
        self.update()

    def _mode_index(self) -> int:
        return next((index for index, (name, _) in enumerate(self.MODES)
                     if name == self._mode), 0)

    def _on_mode_changed(self, index: int) -> None:
        self.set_mode(self.MODES[index][0])

    def set_mode(self, mode: str) -> None:
        """切换显示模式并写盘，下次启动沿用"""
        if mode not in config.VIEW_MODES or mode == self._mode:
            return
        settings.set_view_mode(mode)
        self._mode = mode
        index = self._mode_index()
        if self.mode_combo.currentIndex() != index:
            # 外部（启动恢复 / 测试）直接调进来时把下拉对上；自己触发的这次不用回写，
            # 回写会再发一遍 currentIndexChanged，白白多走一圈
            self.mode_combo.blockSignals(True)
            self.mode_combo.setCurrentIndex(index)
            self.mode_combo.blockSignals(False)
        self._rebuild()

    # ---------- 内部 ----------
    def _current_scroll(self):
        if self.stack.currentIndex() == 0:
            return self.card_scroll
        if self.stack.currentIndex() == 1:
            return self.list_scroll
        return None

    def _page_size(self):
        """当前每页条数；选「全部」返回 None，调用方按不分页处理"""
        value = self.size_combo.currentData()
        return int(value) if str(value).isdigit() else None

    def _saved_size_index(self) -> int:
        """按保存的每页数量定位下拉框选项（0 或其它无效值 -> 全部）"""
        size = settings.current_page_size()
        if size and str(size) in self.PAGE_SIZES:
            return list(self.PAGE_SIZES).index(str(size))
        return list(self.PAGE_SIZES).index(config.PAGE_SIZE_ALL)

    def _on_page_size_changed(self) -> None:
        """每页数量变化时立即写入 ui_settings.json，下次启动沿用"""
        settings.set_page_size(self.size_combo.currentData())
        self._on_filter_changed()

    def _on_filter_changed(self) -> None:
        self._page = 0
        self._rebuild()

    def _turn_page(self, step: int) -> None:
        self._page = max(0, self._page + step)
        self._rebuild()

    def _on_image_updated(self, url: str) -> None:
        """某张图片到达：只重绘用到它的那一行 / 那张卡

        无条件全量刷新会让首屏变成 O(N×M) 次重绘（几百张图 × 几百行），
        GUI 线程被 paintEvent 占满，滚动和点击都会发涩。
        """
        for widget in self._widgets.values():
            widget.refresh_images(url)

    def _apply_filters(self):
        keyword = self.search_edit.text().strip().lower()
        platform_filter = self.platform_combo.currentText()
        live_filter = self.live_combo.currentText()
        record_filter = self.record_combo.currentText()
        monitor_filter = self.monitor_combo.currentText()
        users = self._users
        if keyword:
            users = [user for user in users if keyword in " ".join((
                str(user.get("nickname", "")), str(user.get("remark", "")),
                str(user.get("room_id", "")), str(user.get("desc", "")),
                str(user.get("user_id", "")))).lower()]
        if self.platform_combo.currentIndex() > 0:
            # 关注列表里没有 platform 字段：条目默认就是当前这个平台，别按缺字段一律排除，
            # 否则选「小红书」反而清空整个列表
            users = [user for user in users
                     if str(user.get("platform", config.PLATFORM_NAME)) == platform_filter]
        if live_filter == "正在直播":
            users = [user for user in users if user.get("is_live")]
        elif live_filter == "未开播":
            users = [user for user in users if not user.get("is_live")]
        if record_filter == "录制中":
            users = [user for user in users if user.get("user_id") in self._records]
        elif record_filter == "未录制":
            users = [user for user in users if user.get("user_id") not in self._records]
        if monitor_filter == "自动":
            users = [user for user in users if user.get("auto_record", True)]
        elif monitor_filter == "手动":
            users = [user for user in users if not user.get("auto_record", True)]
        return self._sorted(users)

    def _sort_key(self) -> str:
        return self.SORT_KEYS[self.sort_combo.currentIndex()]

    def _sorted(self, users):
        """按当前排序字段 + 方向排；「默认排序」保持接口返回的顺序"""
        key = self._sort_key()
        if key == "default":
            return users
        reverse = self._sort_desc
        if key == "user":
            return sorted(users, key=lambda u: str(u.get("nickname") or ""), reverse=reverse)
        if key == "last":
            return sorted(users, key=lambda u: str(u.get("last_record") or ""), reverse=reverse)
        if key == "live":
            # 同组内再按粉丝数：直播中的先后顺序也有个说得通的依据
            return sorted(users, key=lambda u: (bool(u.get("is_live")),
                                                int(u.get("fan_count") or 0)), reverse=reverse)
        return sorted(users, key=lambda u: u.get("user_id") in self._records,
                      reverse=reverse)   # 录制状态：录制中的排前面

    def _on_sort_changed(self) -> None:
        self._sort_desc = self.NATURAL_DESC.get(self._sort_key(), True)
        self._on_filter_changed()

    def _on_head_clicked(self, key: str) -> None:
        """表头点击：同一列再点一次换方向，换列则回到该列的自然方向"""
        if self.sort_combo.currentIndex() == self.SORT_KEYS.index(key):
            self._sort_desc = not self._sort_desc
            self._on_filter_changed()
        else:
            self.sort_combo.setCurrentIndex(self.SORT_KEYS.index(key))   # 由 _on_sort_changed 重建

    def filters_active(self) -> bool:
        """是否有筛选条件生效（空状态里要不要给出「清除筛选」）

        排序不算筛选条件：只改了排序却冒出「清除筛选」，点一下连排序也一起丢了。
        直播/录制/监听是空值（-1，框里只有字段名提示）= 不筛；平台是第 0 项「全部平台」= 不筛。
        """
        if self.search_edit.text().strip():
            return True
        if any(combo.currentIndex() >= 0 for combo in
               (self.live_combo, self.record_combo, self.monitor_combo)):
            return True
        return self.platform_combo.currentIndex() > 0

    def clear_filters(self) -> None:
        """一键清掉平台 / 搜索 / 直播 / 录制 / 监听筛选，回到全量列表（排序保持用户自己的选择）"""
        self.search_edit.clear()   # textChanged 会起防抖，但不保证等得到，下面直接重建
        for combo in (self.live_combo, self.record_combo, self.monitor_combo):
            combo.blockSignals(True)
            combo.setCurrentIndex(-1)
            combo.blockSignals(False)
        self.platform_combo.blockSignals(True)
        self.platform_combo.setCurrentIndex(0)   # 回到「全部平台」，它没有空值这一档
        self.platform_combo.blockSignals(False)
        self._on_filter_changed()

    def keyPressEvent(self, event) -> None:
        """键盘可达性：←/→ 翻页，Ctrl+F 聚焦搜索，Esc 清空搜索"""
        key = event.key()
        modifiers = event.modifiers()
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Backspace) and self.prev_btn.isEnabled():
            self._turn_page(-1)
            event.accept()
            return
        if key in (Qt.Key.Key_Right, Qt.Key.Key_Space) and self.next_btn.isEnabled():
            self._turn_page(1)
            event.accept()
            return
        if key == Qt.Key.Key_F and modifiers & Qt.KeyboardModifier.ControlModifier:
            self.search_edit.setFocus()
            self.search_edit.selectAll()
            event.accept()
            return
        if key == Qt.Key.Key_Escape and self.search_edit.text():
            self.search_edit.clear()
            event.accept()
            return
        super().keyPressEvent(event)

    def _clear(self) -> None:
        for layout in (self.card_layout, self.list_layout):
            while layout.count():
                item = layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.setParent(None)
                    widget.deleteLater()
        self._widgets = {}
        self._pool = {}
        self._list_header = None
        self._list_heads = {}
        self._visible_key = None   # 控件全销毁了，就地刷新 fast path 不能再命中

    def _layout_key(self) -> tuple:
        """控件的「形状」特征：任何一项变了都说明子控件结构不同，旧实例不能复用

        主题不计入：控件的配色由 refresh_theme() 就地重刷，换肤不需要整页重建
        （重建会把头像位图、样式解析、布局计算全部重来一遍）。
        """
        if self._mode == "card":
            return ("card", self._card_width)
        return ("list", tuple(sorted(settings.current_list_fields())))

    def _detach(self) -> None:
        """把控件从布局里摘下来留作复用（不销毁）

        翻页 / 搜索 / 轮询刷新都会反复走 _rebuild；若每次都全量销毁再新建，
        几十上百个控件连同头像 QPixmap、样式解析、布局计算都要重来一遍，
        而相邻两轮的可见集合绝大部分是同一批 user_id（尤其轮询刷新时一模一样）。
        """
        self._pool = dict(self._widgets)
        self._widgets = {}
        for layout in (self.card_layout, self.list_layout):
            while layout.count():
                item = layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.hide()   # 摘下来后先藏起来，避免在这轮重建里留残影

    def _add_widget(self, layout, user: dict, card: bool) -> None:
        """差量填充一个控件：优先复用池里同 user_id 的实例，没有再新建"""
        user_id = user.get("user_id")
        widget = self._pool.pop(user_id, None)
        if widget is not None and not widget.sync_user(user):
            widget.setParent(None)   # 结构对不上：旧实例只能丢弃
            widget.deleteLater()
            widget = None
        if widget is None:
            widget = entries.FollowingCard(user, width=self._card_width) if card else entries.FollowingRow(user)
            self._wire(widget)
        layout.addWidget(widget)
        widget.show()
        self._widgets[user_id] = widget

    def _drop_unused(self) -> None:
        """这轮没被复用的控件：销毁回收"""
        for widget in self._pool.values():
            widget.setParent(None)
            widget.deleteLater()
        self._pool = {}

    def _rebuild(self) -> None:
        scroll = self._current_scroll()
        position = scroll.verticalScrollBar().value() if scroll is not None else 0

        self._sync_card_width()   # 重建前先刷新卡片宽度，保证整行铺满
        filtered = self._apply_filters()
        size = self._page_size()
        pages = max(1, -(-len(filtered) // size)) if size else 1
        self._page = max(0, min(self._page, pages - 1))
        start = self._page * size if size else 0
        items = filtered[start:start + size] if size else filtered

        key = self._layout_key()
        visible_key = (key, self._page, tuple(user.get("user_id") for user in items))
        self._page_items = items   # 主播名列宽按当页最长昵称实测，翻页 / 筛选后随之变化
        if items and visible_key == self._visible_key and self._refresh_inplace(items):
            # 轮询最常见的形状是「同一批人、状态微调」：可见集合没变就逐个灌新数据，
            # 整池摘挂、列宽重算、布局失效全部省掉
            self._update_footer(filtered, pages)
            self._sync_list_widths()   # 昵称可能改了：主播名列宽跟着当页最长名走
            self._apply_header_arrows()   # 可见集合没变也可能只换了排序字段 / 方向
            return

        if key != self._signature:
            self._clear()          # 结构变了（模式 / 卡片宽度 / 列配置）：没法复用
            self._signature = key
        else:
            self._detach()         # 结构一致：摘下来，交给差量填充逐个复用

        if not items:
            # 三种「这一页没东西」得分开说：还在转、压根没读到、被筛没了
            loading_now = self._loading and not self._users
            if loading_now:
                hint = self._hint_text(self._hint_step)
            elif not self._users:
                hint = "没读到关注用户：登录态没配好或刷新失败都会这样，点下面重试"
            else:
                hint = "没有符合条件的关注用户"
            self.empty_label.setText(hint)
            # 正在读的时候两枚都没意义：重试的那次请求已经在飞了，筛选要等数据回来才谈得上
            self.empty_refresh.setVisible(not loading_now)
            # 筛出来的空结果要给出出口，否则用户不知道是哪个下拉框把人筛没了
            self.empty_clear.setVisible(not loading_now
                                       and bool(self._users) and self.filters_active())
            self.stack.setCurrentIndex(2)
        elif self._mode == "card":
            for user in items:
                self._add_widget(self.card_layout, user, card=True)
            self.stack.setCurrentIndex(0)
        else:
            self._sync_list_widths()   # 重建前先按当前视口宽度分配各列宽
            if self._list_header is None:
                self._list_header = self._build_list_header()
            else:
                self._apply_header_widths()
            self._apply_header_arrows()
            self.list_layout.addWidget(self._list_header)
            self._list_header.show()
            for user in items:
                self._add_widget(self.list_layout, user, card=False)
            self.stack.setCurrentIndex(1)
            # 列表页切出来后滚动区才拿到真实宽度：再算一次，首帧就是分配后的列宽
            # （切页前 list_scroll 不可见，量到的是旧宽度，见 _sync_list_widths）
            self._sync_list_widths()
            self._width_timer.start()   # 保险：滚动条占位等布局稳定后再补一次
        self._sync_hint_timer()   # 页码定下来了才知道那拍动画该不该转
        self._drop_unused()
        self._visible_key = visible_key

        for user_id, stats in self._records.items():
            widget = self._widgets.get(user_id)
            if widget is not None:
                widget.set_record(stats)

        for user_id, pending in self._pendings.items():
            widget = self._widgets.get(user_id)
            if widget is not None:
                widget.set_pending(pending)

        self.card_container.sync_height()   # 卡片行数变化后重新计算内容高度
        target = self._current_scroll()
        if target is not None and position:
            target.verticalScrollBar().setValue(
                min(position, target.verticalScrollBar().maximum()))

        self._update_footer(filtered, pages)

    def _refresh_inplace(self, items) -> bool:
        """可见集合没变时，就地把新数据灌进现成控件；结构对不上则回退到整页重建"""
        for user in items:
            widget = self._widgets.get(user.get("user_id"))
            if widget is None or not widget.sync_user(user):
                return False
        self.card_container.sync_height()
        return True

    def _update_footer(self, filtered, pages) -> None:
        """底部汇总与翻页按钮状态"""
        self._filtered_total = len(filtered)
        self._refresh_summary()
        self.page_label.setText(f"{self._page + 1}/{pages}")
        self.prev_btn.setEnabled(self._page > 0)
        self.next_btn.setEnabled(self._page < pages - 1)

    def _refresh_summary(self) -> None:
        """只重算底部那行「共 N · 直播中 N · 录制中 N」

        单独拆出来是因为录制起止不走整页重建：以前「录制中」只在重建时更新，
        于是开始录两路之后底部还停在 0，跟标题栏的「录制中 · 2 路」当面对不上。
        """
        live_count = sum(1 for user in self._users if user.get("is_live"))
        summary = f"共 {len(self._users)} 位 · 直播中 {live_count} · 录制中 {len(self._records)}"
        if self._filtered_total != len(self._users):
            summary += f" · 筛选后 {self._filtered_total} 位"
        self.count_label.setText(summary)

    def refresh_totals(self, text: str) -> None:
        """主窗口把「录制中合计 / 磁盘剩余」写进底部（录制器最该盯的两个数）"""
        if text:
            self.total_label.setText(text)
            self.total_label.setToolTip(
                "磁盘剩余低于 500MB 时不再开新录，低于 120MB 时当前这段会正常封箱后停止")

    def _build_list_header(self) -> QWidget:
        """列表表头：与行使用同一份「显示字段」配置，保证列一一对应

        可排序的列用 HeadLabel（点击切排序），箭头由 _apply_header_arrows 事后写上，
        与工具栏排序下拉共用同一份状态。
        """
        fields = set(settings.current_list_fields())
        header = QFrame()
        header.setObjectName("listHeader")
        header.setFixedHeight(30)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(10, 0, 8, 0)
        layout.setSpacing(6)
        self._list_heads = {}
        self._head_captions = {"user": "主播名"}
        user_head = self._head_label("user", "主播名")
        user_head.setFixedWidth(entries.FollowingRow.USER_WIDTH)   # 与行同一份列宽
        self._list_heads["user"] = user_head
        layout.addWidget(user_head)
        layout.addStretch(1)   # 与行一致：列间弹性间距均分富余宽度
        for key, caption, width in entries.FollowingRow.columns():
            if key not in fields:
                continue
            self._head_captions[key] = caption
            label = self._head_label(key, caption)
            if key in entries.FollowingRow.CENTERED_KEYS:
                label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setFixedWidth(width)   # 宽度由 _sync_list_widths 分配，行与表头共用
            layout.addWidget(label)
            self._list_heads[key] = label
            if key != "action":
                layout.addStretch(1)   # 「操作」后不加：它贴右边界收尾
        return header

    def _head_label(self, key: str, caption: str) -> QLabel:
        """表头单元格：可排序的列换成可点击的 HeadLabel，普通列仍是标签"""
        if key not in self.SORTABLE:
            label = QLabel(caption)
        else:
            label = widgets.HeadLabel(caption)
            label.clicked.connect(lambda k=key: self._on_head_clicked(k))
        label.setObjectName("listHead")
        return label

    def _apply_header_arrows(self) -> None:
        """把当前排序字段的方向写进表头标题：表头控件是复用的，每次重建都要重刷"""
        key = self._sort_key()
        for head_key, label in self._list_heads.items():
            caption = self._head_captions.get(head_key, "")
            if head_key == key and head_key in self.SORTABLE:
                caption = f"{caption} {'↓' if self._sort_desc else '↑'}"
            if label.text() != caption:
                label.setText(caption)

    def _wire(self, widget) -> None:
        """连线：一律从控件自身读用户数据

        控件会被复用，连线时闭包捕获的那份 dict 会过期（比如开播状态、
        房间号停在重建前那一刻），因此菜单与录制指令都用 w.user() 现取。
        """
        widget.more.clicked.connect(
            lambda checked=False, w=widget: self._show_menu(w.user(), w.more))
        if isinstance(widget, entries.FollowingCard):
            # 卡片上唯一的改状态入口：「···」（或右键卡片）里的「开启 / 关闭自动录制」
            # 右键也拿「···」当锚点：菜单只有一个入口，就该从同一个位置长出来
            widget.menu_requested.connect(lambda w=widget: self._show_menu(w.user(), w.more))
        if isinstance(widget, entries.FollowingRow):
            widget.action_button.clicked.connect(
                lambda checked=False, w=widget: self._emit_action(
                    "stop" if w.user().get("user_id") in self._records else "record", w.user()))

    def _emit_action(self, action: str, user: dict) -> None:
        """发出录制/停止指令前先给按钮乐观反馈，避免等进度回调才变化"""
        user_id = user.get("user_id", "")
        if action == "record":
            self.set_pending_action(user_id, ("停止录制", True))
        elif action == "stop":
            self.set_pending_action(user_id, ("停止中…", False))
        self.action.emit(action, user)

    def set_pending_action(self, user_id: str, pending) -> None:
        """记录点击后的乐观状态：轮询刷新会重建整页，存到视图层才能在重建后回填"""
        if pending is None:
            self._pendings.pop(user_id, None)
        else:
            self._pendings[user_id] = pending
        widget = self._widgets.get(user_id)
        if isinstance(widget, entries.FollowingRow):
            widget.set_pending(pending)

    def clear_pending(self, user_id: str) -> None:
        """主窗口判定指令未生效时，撤销乐观状态回到真实状态"""
        self.set_pending_action(user_id, None)

    def _build_menu(self, user: dict) -> widgets.CalloutMenu:
        """组「更多操作」菜单：录制动作 / 查看与复制 / 自动录制开关，三组之间画分隔线

        单独拆出来是为了能在测试里直接检查菜单项（弹出后菜单归事件循环管，一调就把冒烟测试卡住）。
        """
        menu = widgets.CalloutMenu(self)
        recording = user.get("user_id") in self._records
        if recording:
            menu.addAction("停止录制", lambda: self._emit_action("stop", user))
        else:
            action = menu.addAction("开始录制", lambda: self._emit_action("record", user))
            action.setEnabled(bool(user.get("is_live") and user.get("video_url")))
            if not user.get("is_live"):
                action.setToolTip("该用户当前未开播")
        menu.addSeparator()
        menu.addAction("打开录制目录", lambda: self.action.emit("open", user))
        menu.addAction("复制房间号", lambda: self.action.emit("copy", user))
        # 实时预览：单独一档，因为它会多开一路拉流，跟"看一眼信息"不是一类动作
        if preview.is_open(user.get("user_id")):
            menu.addAction("停止预览", lambda: self.action.emit("preview_stop", user))
        else:
            action = menu.addAction("实时预览", lambda: self.action.emit("preview", user))
            reason = _preview_blocker(user)
            action.setEnabled(not reason)
            if reason:
                action.setToolTip(reason)
            elif recording:
                action.setToolTip("正在录制中：预览会再开一路拉流连接")
        menu.addSeparator()
        auto_on = bool(user.get("auto_record", True))
        menu.addAction("关闭自动录制" if auto_on else "开启自动录制",
                       lambda: self._request_auto_toggle(user, not auto_on))
        return menu

    def _show_menu(self, user: dict, anchor: QWidget) -> None:
        """从「···」旁边弹出菜单：尖角指着那三个点，别让人猜菜单是谁开的"""
        widgets.show_callout_menu(self._build_menu(user), anchor)

    def _request_auto_toggle(self, user: dict, enabled: bool) -> None:
        """切换自动录制开关：由主窗口落库并回写界面"""
        self.action.emit("auto", dict(user, auto_record=enabled))


def _preview_blocker(user: dict) -> str:
    """预览能不能开，不能开的话原因写在菜单项上——只置灰不解释是最容易被当成 bug 的

    返回空串表示可以预览。顺序按「谁最可能坏」排：机器上没有 ffplay 是全局问题，
    未开播和没有流地址是这一个主播的问题。
    """
    if not preview.available():
        return "没找到 ffplay.exe，无法预览（它和 ffmpeg 在同一个包里）"
    if not user.get("is_live"):
        return "该用户当前未开播"
    if not user.get("video_url"):
        return "没有直播流地址，刷新一下关注列表再试"
    return ""
