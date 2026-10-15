"""关注列表里「一个主播」的两种展示：卡片与列表行"""

from xhs import settings
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget
from xhs.ui import theme, widgets


def short_last_record(text: str) -> str:
    """列表行里的「最近录制时间」：年月日 + 时分秒全显示

    秒位是判断「两次录制隔了多久」的关键（同一分钟内可能连着录两场），
    省掉它会让相邻两行看起来一模一样。列宽按实测串宽给，不靠省略。
    """
    if len(text) < 19 or text[4] != "-" or text[7] != "-" or text[10] != " " or text[13] != ":":
        return text or "—"
    return text[:19]              # 2026-09-18 10:17:22


class FollowingCard(QFrame):
    """关注用户卡片：封面（含状态条）+ 最近录制 + 头像昵称标签 + 自动录制标签 + 更多操作"""

    WIDTH = 186            # 兜底宽度，实际宽度由 FollowingView 按可视宽度铺满计算
    RATIO = 97 / 172       # 封面高宽比：卡片变宽时封面同步放大
    MIN_COVER_HEIGHT = 88
    BADGES_WIDTH = widgets.LiveBadge.SIZE + widgets.AutoRecordTag.SIZE + 8
    # 昵称右侧两枚标签占宽：直播标 + 24 标签 + 两道 4px 间距。按两枚的真实控件边长算，
    # 不再假设它们等大（直播标 14，24 环 12，两枚的墨迹都按昵称那 12px 定）

    menu_requested = Signal()                   # 右键卡片：跟「···」开同一个菜单

    def __init__(self, user: dict, width: int = 0, parent=None):
        super().__init__(parent)
        self.user_id = user.get("user_id", "")
        self._user = dict(user)
        self._width = int(width or self.WIDTH)
        self._cover_height = max(self.MIN_COVER_HEIGHT, int(round(self._width * self.RATIO)))
        self.setObjectName("followCard")
        self.setFixedWidth(self._width)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.cover = widgets.CoverBox(user, self._width, self._cover_height)
        # 关注列表接口没有直播封面字段，用头像作为封面图（缺图时回落到渐变占位）
        self.cover.set_url(user.get("avatar", ""), placeholder=(user.get("nickname") or "?")[:1])
        outer.addWidget(self.cover)

        info = QHBoxLayout()
        info.setContentsMargins(8, 5, 8, 8)
        info.setSpacing(7)
        self.avatar = widgets.RoundedImage(40, 40, radius=6, seed=self.user_id,
                                   placeholder=(user.get("nickname") or "?")[:1])
        self.avatar.set_url(user.get("avatar", ""))
        info.addWidget(self.avatar, 0, Qt.AlignmentFlag.AlignTop)

        text_col = QVBoxLayout()
        text_col.setSpacing(3)
        nick_row = QHBoxLayout()
        nick_row.setSpacing(4)
        self.nick = QLabel()
        self.nick.setObjectName("cardNick")
        self.nick.setToolTip(user.get("nickname", ""))
        # 直播标识（裸符号无环）+ 自动录制标签（圆环）：昵称右侧两枚等宽自绘标签，
        # 全是只读状态说明，改状态走「···」菜单
        self.live_badge = widgets.LiveBadge()
        self.auto_tag = widgets.AutoRecordTag()
        nick_row.addWidget(self.nick)
        nick_row.addWidget(self.live_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        nick_row.addWidget(self.auto_tag, 0, Qt.AlignmentFlag.AlignVCenter)
        nick_row.addStretch()
        text_col.addLayout(nick_row)

        tag_row = QHBoxLayout()
        tag_row.setSpacing(4)
        self.platform_pill = widgets._platform_pill()   # 留引用：换主题就地重套配色
        tag_row.addWidget(self.platform_pill)
        tag_row.addStretch()
        # 「···」挂在平台标签那一行的右端，而不是整个信息列的右端：它本来就贴着
        # 卡片底边，占的却是昵称那一行的宽度。挪下来之后昵称行拿回这 26px
        self.more = QPushButton("•••")
        self.more.setObjectName("moreBtn")
        self.more.setFixedSize(22, 22)
        self.more.setCursor(Qt.CursorShape.PointingHandCursor)
        self.more.setToolTip("更多操作")
        tag_row.addWidget(self.more, 0, Qt.AlignmentFlag.AlignBottom)
        text_col.addLayout(tag_row)
        text_col.addStretch()      # 两行顶到信息列上沿，跟头像齐平
        info.addLayout(text_col, 1)

        outer.addLayout(info)

        self._sync_live_badge()
        self._elide_nick()
        self._sync_auto_tag()

    def _elide_nick(self) -> None:
        # 可视宽度 - 左右边距 - 头像 - 头像间距 - 两枚标签（含两道间距）
        # 「···」现在在平台标签那一行，不再从昵称这一行扣宽度
        width = self._width - 16 - 40 - 7 - self.BADGES_WIDTH
        self.nick.setText(QFontMetrics(self.nick.font()).elidedText(
            self._user.get("nickname", ""), Qt.TextElideMode.ElideRight, width))

    def _sync_live_badge(self) -> None:
        # 未开播时隐藏（隐藏控件不占布局），但昵称省略宽度按两枚标签常量预留，
        # 同一行里开播 / 未开播卡片的昵称截断位置保持一致
        self.live_badge.setVisible(bool(self._user.get("is_live")))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._elide_nick()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        # 样式表字体在 polish 之后才生效，字体/样式变化时按真实字体重新省略
        if event.type() in (QEvent.Type.StyleChange, QEvent.Type.FontChange):
            self._elide_nick()

    def contextMenuEvent(self, event) -> None:
        """右键弹「更多操作」：卡片上除了「···」没别的可点，菜单不该只藏在一个小按钮里"""
        self.menu_requested.emit()
        event.accept()

    def _sync_auto_tag(self) -> None:
        """「24」标签：开了自动录制才挂出来，关掉就是不显示

        它不再能按：改状态只有「···」菜单一个入口。卡片上能按的东西越多，误触越多，
        而关掉以后环内浓淡两档也失去了意义——省掉一档，读的人少一次猜疑。
        """
        enabled = bool(self._user.get("auto_record", True))
        self.auto_tag.setVisible(enabled)
        self.auto_tag.setToolTip("自动录制：已开启（24 小时监控，开播即自动录制）\n"
                                 "改状态请用卡片右下角「···」菜单")
        self.auto_tag.setAccessibleName(f"{self._user.get('nickname', '')} 自动录制：已开启")

    def user(self) -> dict:
        """当前控件承载的用户数据：菜单 / 指令都从控件现取，避免闭包捕获旧 dict"""
        return self._user

    def sync_user(self, user: dict) -> bool:
        """复用控件时灌入新的用户数据；卡面结构固定（宽度已在布局特征里），总能就地刷新"""
        self._user = dict(user)
        self.cover.sync_user(user)
        self.avatar.set_url(user.get("avatar", ""))
        self._sync_live_badge()
        self._elide_nick()
        self._sync_auto_tag()     # 标签在不在、悬浮说明怎么写
        return True

    def set_auto_record(self, enabled: bool) -> None:
        self._user["auto_record"] = bool(enabled)
        self._sync_auto_tag()

    def set_record(self, stats) -> None:
        self.cover.set_record(stats)

    def set_last_record(self, text: str) -> None:
        self._user["last_record"] = text
        self.cover.set_last_record(text)

    def refresh_theme(self) -> None:
        """换主题：手写配色的胶囊就地重套一次，自绘部分靠 update 走新调色板

        早先的做法是把主题计入布局签名、切主题就重建整页，几百行头像
        连同图片缓存命中都要重来一遍；这里能就地刷完，就不必再动了。
        """
        widgets._apply_platform_pill(self.platform_pill)
        self.live_badge.update()
        self.auto_tag.update()   # 「24」的描边与字色每次重绘现取主题
        self.cover.update()
        self.avatar.update()

    def refresh_images(self, url: str = "") -> None:
        self.avatar.refresh(url)
        self.cover.refresh(url)

class FollowingRow(QFrame):
    """关注用户列表行：表格式紧凑布局（列显隐由「显示字段」控制）"""

    USER_WIDTH = 216            # 主播名列宽：运行时按当页最长昵称实测（见 FollowingView._sync_list_widths）
    MIN_USER_WIDTH = 120        # 下限：全是短名也放得下头像 + 几个字
    MAX_USER_WIDTH = 360        # 上限：再长的昵称省略号截断，不让一列挤掉整行
    PILL_WIDTH = 68             # 胶囊三列（平台 / 直播 / 录制）宽度：按内容给
    ROOM_WIDTH = 0              # 房间号列宽：0 = 未分配，取 room_col_width() 的实测值
    MIN_ROOM_WIDTH = 124        # 下限：表头「房间号」+ 常见位数的房间号放得下
    MAX_ROOM_WIDTH = 240        # 上限：再长的房间号省略号截断，不让一列挤掉整行
    LAST_WIDTH = 0              # 最近录制时间列宽：0 = 未分配，取 last_col_width() 的实测值
    ACTION_WIDTH = 106          # 操作列宽：固定值，按钮按内容排布，不跟着窗口变宽
    # 各列都不拉伸：列宽按内容定死，富余宽度交给列间弹性间距均分（行与表头同步）
    STRETCHES = {"platform": 0, "live": 0, "record": 0, "room": 0, "last": 0}
    # 胶囊列：表头与内容都居中（房间号 / 时间仍是左对齐文本）
    CENTERED_KEYS = {"platform", "live", "record"}
    # 可开关的列：key → (表头, 宽度)，顺序即列顺序，与 LIST_FIELDS 的 key 对应
    # 宽度是各列的下限；运行时按视口宽度重算（见 columns / _sync_list_widths）
    COLUMNS = (("platform", "平台", 68), ("live", "直播状态", 68),
               ("record", "录制状态", 68), ("room", "房间号", 124),
               ("last", "最近录制时间", 136), ("action", "操作", 106))
    _LAST_COL_WIDTH = None   # 实测缓存（QApplication 创建后才有字体可用）
    _LIST_FONT = None

    @classmethod
    def _list_font(cls):
        """列表文本的度量字体：列表里所有文本统一 12px"""
        if cls._LIST_FONT is None:
            font = QLabel().font()
            font.setPixelSize(12)
            cls._LIST_FONT = font
        return cls._LIST_FONT

    @classmethod
    def last_col_width(cls) -> int:
        """最近录制时间列宽：用当前字体实测完整时间串（年月日 + 时分秒）再加边距"""
        if cls._LAST_COL_WIDTH is None:
            advance = QFontMetrics(cls._list_font()).horizontalAdvance("2026-09-18 10:17:22")
            cls._LAST_COL_WIDTH = max(136, advance + 12)
        return cls._LAST_COL_WIDTH

    @classmethod
    def room_col_width(cls, room_ids=()) -> int:
        """房间号列宽：按当页最长的房间号实测，夹在上下限之间

        房间号是纯数字、字体度量稳定，不必像昵称那样先取行内控件的字体。
        """
        metrics = QFontMetrics(cls._list_font())
        advance = max((metrics.horizontalAdvance(str(rid)) for rid in room_ids), default=0)
        return max(cls.MIN_ROOM_WIDTH, min(advance + 12, cls.MAX_ROOM_WIDTH))

    @classmethod
    def columns(cls) -> tuple:
        """COLUMNS 的运行时版本：各列宽替换为「按视口宽度分配」后的值

        时间列直接用分配值（LAST_WIDTH=0 表示还没分配过，退回实测下限）：
        取 max 会让它在窗口变窄后回不去，行尾留白被挤成横向溢出。
        """
        runtime = {"platform": cls.PILL_WIDTH, "live": cls.PILL_WIDTH,
                   "record": cls.PILL_WIDTH, "room": cls.ROOM_WIDTH or cls.MIN_ROOM_WIDTH,
                   "last": cls.LAST_WIDTH or cls.last_col_width(),
                   "action": cls.ACTION_WIDTH}
        return tuple((key, caption, runtime.get(key, width))
                     for key, caption, width in cls.COLUMNS)

    HEIGHT = 54

    def __init__(self, user: dict, parent=None):
        super().__init__(parent)
        self.user_id = user.get("user_id", "")
        self._user = dict(user)
        self._record = None
        self._pending = None   # 点击后的乐观按钮状态 (文案, 是否可用)，None 表示按录制状态推导
        self.fields = set(settings.current_list_fields())
        self.setObjectName("followRow")
        self.setFixedHeight(self.HEIGHT)

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 6, 8, 6)
        row.setSpacing(6)

        # 用户列：头像 + 昵称（备注）；列固定，内部的昵称 / 备注可单独关掉
        user_cell = QWidget()
        user_cell.setFixedWidth(self.USER_WIDTH)
        self._user_cell = user_cell
        user_layout = QHBoxLayout(user_cell)
        user_layout.setContentsMargins(0, 0, 0, 0)
        user_layout.setSpacing(8)
        self.avatar = widgets.RoundedImage(32, 32, radius=6, seed=self.user_id,
                                   placeholder=(user.get("nickname") or "?")[:1])
        self.avatar.set_url(user.get("avatar", ""))
        user_layout.addWidget(self.avatar, 0, Qt.AlignmentFlag.AlignVCenter)
        name_col = QVBoxLayout()
        name_col.setSpacing(1)
        # 可能不显示的控件一律挂在行（self）上：既不会被 Python 回收，
        # 也不会变成无主的顶层窗；不入布局时必须显式 hide，否则会跟着行一起显示
        self.nick = QLabel(self)
        self.nick.setObjectName("listCell")
        self.nick.setToolTip(user.get("nickname", ""))
        self.nick.setText(QFontMetrics(self.nick.font()).elidedText(
            user.get("nickname", ""), Qt.TextElideMode.ElideRight,
            max(120, self.USER_WIDTH - 40)))
        if "nick" in self.fields:
            name_col.addWidget(self.nick)
        else:
            self.nick.hide()
        if user.get("remark"):
            self.remark = QLabel(self)
            self.remark.setObjectName("cardFan")
            self.remark.setToolTip(user["remark"])
            self.remark.setText(QFontMetrics(self.remark.font()).elidedText(
                user["remark"], Qt.TextElideMode.ElideRight,
                max(120, self.USER_WIDTH - 40)))
            if "remark" in self.fields:
                name_col.addWidget(self.remark)
            else:
                self.remark.hide()
        user_layout.addLayout(name_col, 1)
        row.addWidget(user_cell)   # 各列宽度由 _sync_list_widths 一次算好，行与表头共用
        row.addStretch(1)          # 列间弹性间距：富余宽度均分到每个间隔，行与表头同步

        widths = {key: width for key, _caption, width in self.columns()}
        # 各列都固定宽（宽度由 _sync_list_widths 一次算好），窗口变化时只改宽不重建
        self._pill_boxes = {}
        self.platform_pill = widgets._platform_pill()   # 留引用：换主题时要就地重套配色
        if "platform" in self.fields:
            self._pill_boxes["platform"] = self._fixed_box(self.platform_pill,
                                                           widths["platform"])
            row.addWidget(self._pill_boxes["platform"], self.STRETCHES["platform"])
            row.addStretch(1)
        # 直播状态胶囊同样始终创建（开播状态随时会变），只是不入布局时先隐藏
        self.live_label = widgets._state_pill(user)
        self.live_label.setParent(self)
        if "live" in self.fields:
            self._pill_boxes["live"] = self._fixed_box(self.live_label, widths["live"])
            row.addWidget(self._pill_boxes["live"], self.STRETCHES["live"])
            row.addStretch(1)
        else:
            self.live_label.hide()

        # 录制状态：与直播状态一致，用胶囊标签只区分「录制中 / 空闲」
        # 控件始终创建（录制推送随时会来），只是不显示时不加入布局
        self.record_label = QLabel(self)
        self.record_label.setObjectName("listCell")
        if "record" in self.fields:
            self._pill_boxes["record"] = self._fixed_box(self.record_label, widths["record"])
            row.addWidget(self._pill_boxes["record"], self.STRETCHES["record"])
            row.addStretch(1)
        else:
            self.record_label.hide()

        room_id = str(user.get("room_id") or "")
        self.room_label = QLabel(room_id or "—", self)
        self.room_label.setObjectName("listRoom")
        self.room_label.setFixedWidth(widths["room"])
        self.room_label.setToolTip(room_id or "当前未开播，暂无房间号")
        if room_id:
            self.room_label.setText(QFontMetrics(self.room_label.font()).elidedText(
                room_id, Qt.TextElideMode.ElideMiddle, widths["room"] - 4))
        if "room" in self.fields:
            row.addWidget(self.room_label, self.STRETCHES["room"])
            row.addStretch(1)
        else:
            self.room_label.hide()

        self.last_label = QLabel(self)
        self.last_label.setObjectName("listTime")
        self.last_label.setFixedWidth(widths["last"])   # 时间列不拉伸，按分配值固定
        self.last_label.setToolTip(f"最近一次录制：{user.get('last_record') or '暂无记录'}")
        self.last_label.setText(QFontMetrics(self.last_label.font()).elidedText(
            short_last_record(user.get("last_record") or ""),
            Qt.TextElideMode.ElideRight, widths["last"] - 4))
        if "last" in self.fields:
            row.addWidget(self.last_label, self.STRETCHES["last"])
            row.addStretch(1)
        else:
            self.last_label.hide()

        # 必须挂在行上：不显示时它不在布局里，没有 Qt 父对象就会被 Python 回收，
        # 里面的按钮随之析构，_wire() 连线时抛「Signal source has been deleted」
        actions = self._action_box = QWidget(self)
        actions.setFixedWidth(widths["action"])
        action_layout = QHBoxLayout(actions)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(4)
        self.action_button = QPushButton("开始录制")
        self.action_button.setObjectName("ghostBtn")
        self.action_button.setFixedHeight(26)
        self.action_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.more = QPushButton("•••")
        self.more.setObjectName("moreBtn")
        self.more.setFixedSize(22, 22)
        self.more.setCursor(Qt.CursorShape.PointingHandCursor)
        self.more.setToolTip("更多操作")
        action_layout.addWidget(self.action_button, 1)
        action_layout.addWidget(self.more, 0)
        self._sync_more_tip()   # 自动录制状态的行内提示跟着当前用户数据走
        # 最后一列后的弹性间距已由各列自带，「操作」列直接跟在后面贴右
        if "action" in self.fields:
            row.addWidget(actions)
        else:
            actions.hide()

        self._refresh_record()
        self._relayout_text()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._relayout_text()

    def apply_widths(self) -> None:
        """窗口变化后就地更新各列宽（不重建行），并重算省略文本

        每列都是固定宽：富余宽度停在尾部留白里，不由任何一列吸收。
        """
        widths = {key: width for key, _caption, width in self.columns()}
        targets = [(self._user_cell, self.USER_WIDTH),
                   (self.room_label, widths["room"]), (self.last_label, widths["last"]),
                   (self._action_box, widths["action"])]
        targets += [(box, widths[key]) for key, box in self._pill_boxes.items()]
        for widget, width in targets:
            if widget.minimumWidth() != width:
                widget.setFixedWidth(width)
        self._relayout_text()

    def _relayout_text(self) -> None:
        """列宽随窗口拉伸后，按各列的实际宽度重算省略文本

        各列都有拉伸权重，真实宽度只有布局完成后才知道；
        固定按 COLUMNS 的下限来省略，多出来的宽度就白白浪费了。
        """
        if not hasattr(self, "room_label"):   # __init__ 里 setFixedHeight 会先触发一次 resize
            return
        cell = self.avatar.parentWidget()
        text_w = max(120, cell.width() - 40)   # 头像 32 + 间距 8
        fm = QFontMetrics(self.nick.font())
        self.nick.setText(fm.elidedText(
            self._user.get("nickname", ""), Qt.TextElideMode.ElideRight, text_w))
        if hasattr(self, "remark"):
            self.remark.setText(QFontMetrics(self.remark.font()).elidedText(
                self._user.get("remark", ""), Qt.TextElideMode.ElideRight, text_w))
        room_id = str(self._user.get("room_id") or "")
        self.room_label.setText(QFontMetrics(self.room_label.font()).elidedText(
            room_id, Qt.TextElideMode.ElideMiddle,
            max(self.room_label.minimumWidth(), self.room_label.width()) - 4)
            if room_id else "—")
        self.last_label.setText(QFontMetrics(self.last_label.font()).elidedText(
            short_last_record(self._user.get("last_record") or ""), Qt.TextElideMode.ElideRight,
            max(self.last_label.minimumWidth(), self.last_label.width()) - 4))

    @staticmethod
    def _fixed_box(widget: QWidget, width: int, fixed: bool = True) -> QWidget:
        box = QWidget()
        if fixed:
            box.setFixedWidth(width)
        else:
            box.setMinimumWidth(width)   # 交给布局拉伸，宽度只是下限
        layout = QHBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addStretch(1)
        layout.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addStretch(1)
        return box

    def _refresh_record(self) -> None:
        if self._record:
            # 与标题栏同轴：绿 =「我在产出」。红留给出事（登录态失效），
            # 品牌红不再兼任状态色 —— 浅底上 accent_soft 和 err 肉眼分不开。
            # 卡片封面那枚红点不动：它是录制指示灯的拟物，跟胶囊不是一套语义。
            theme._pill_style(self.record_label, "录制中", theme._rgb("ok"),
                        theme._rgba("ok", 0.16), widgets.STATE_PILL_WIDTH)
            # 实时大小 / 速度 / 用时收进悬浮提示
            self.record_label.setToolTip(" · ".join(part for part in (
                "录制中", self._record.get("size"), self._record.get("speed"),
                self._record.get("elapsed")) if part))
        else:
            theme._pill_style(self.record_label, "空闲", theme._rgba("text", 0.55),
                        theme._rgba("overlay", 0.07), widgets.STATE_PILL_WIDTH)
            self.record_label.setToolTip("当前没有进行中的录制")
        self._sync_action()

    def user(self) -> dict:
        """当前控件承载的用户数据：菜单 / 指令都从控件现取，避免闭包捕获旧 dict"""
        return self._user

    def sync_user(self, user: dict) -> bool:
        """复用控件时灌入新的用户数据

        返回 False 表示结构对不上（备注的有无 / 列配置变化），调用方需要重建一行：
        备注标签是「有才创建」，没法在现成的行里凭空补出来。
        """
        has_remark = bool(user.get("remark"))
        if has_remark != hasattr(self, "remark") or set(settings.current_list_fields()) != self.fields:
            return False
        self._user = dict(user)
        self.apply_widths()   # 列宽被新一轮数据实测更新过就同步
        self.avatar.set_url(user.get("avatar", ""))
        self.nick.setToolTip(user.get("nickname", ""))
        if has_remark:
            self.remark.setToolTip(user["remark"])
        widgets._apply_state_pill(self.live_label, bool(user.get("is_live")))
        room_id = str(user.get("room_id") or "")
        self.room_label.setToolTip(room_id or "当前未开播，暂无房间号")
        self._relayout_text()   # 文本统一在这里按实际列宽重算省略
        self._sync_more_tip()   # 自动录制状态也跟着最新数据走
        self._sync_action()     # 「开始录制」是否可用取决于最新的开播状态
        return True

    def _sync_action(self) -> None:
        """让「开始/停止录制」按钮立刻反映录制状态（点击后先乐观反馈）"""
        if self._pending is not None:
            text, enabled = self._pending
        else:
            recording = bool(self._record)
            text = "停止录制" if recording else "开始录制"
            enabled = bool(recording or self._user.get("is_live"))
        if text != self.action_button.text() or enabled != self.action_button.isEnabled():
            self.action_button.setText(text)
            self.action_button.setEnabled(enabled)

    def set_pending(self, pending) -> None:
        """设置点击后的临时按钮状态，(文案, 是否可用) 或 None 表示恢复推导状态"""
        self._pending = pending
        self._sync_action()

    def set_record(self, stats) -> None:
        self._record = stats
        self._pending = None
        self._refresh_record()

    def set_last_record(self, text: str) -> None:
        self._user["last_record"] = text
        self.last_label.setToolTip(f"最近一次录制：{text or '暂无记录'}")
        self._relayout_text()

    def set_auto_record(self, enabled: bool) -> None:
        """自动录制开关回写：行内没有专用开关，状态落在数据与「···」的悬浮提示上

        菜单本身是每次打开现建的，数据一更新，里面的「关闭 / 开启自动录制」
        立刻就是正确的那条，不必等轮询重建整页。
        """
        self._user["auto_record"] = bool(enabled)
        self._sync_more_tip()

    def _sync_more_tip(self) -> None:
        enabled = bool(self._user.get("auto_record", True))
        self.more.setToolTip("更多操作（自动录制：已开启）" if enabled
                             else "更多操作（自动录制：已关闭）")

    def refresh_theme(self) -> None:
        """换主题：三个手写配色的胶囊就地重套，行本身不重建"""
        if "platform" in self._pill_boxes:
            widgets._apply_platform_pill(self.platform_pill)
        widgets._apply_state_pill(self.live_label, bool(self._user.get("is_live")))
        self._refresh_record()
        self.avatar.update()

    def refresh_images(self, url: str = "") -> None:
        self.avatar.refresh(url)
