"""预览卡片：给 ffplay 那块画面包一层和主程序同风格的边框

ffplay 自己的窗口只能给到「无边框 + 指定尺寸」，主题一律管不着，所以外观只能由外层画：
这里摆一张自绘卡片，再把它的窗口 SetParent 进来当成卡片的内容区。句柄、嵌入、摆位这些
Win32 的事在 xhs/preview.py。

卡片必须是**不透明**窗口，圆角靠按设备像素上的窗口裁剪区域裁出来 —— 两点都是探针定下来的：
Qt 的逐像素透明（WA_TranslucentBackground）在 Windows 上是 layered window 实现的，而 layered
窗口不合成原生子窗口，嵌进来的画面会整个不画（实测亮像素 0%）；Qt 的 setMask 又只有逻辑
像素的精度，200% 缩放下台阶粗成一串点。裁剪区域是窗口级的，原生子窗口照样显示，
代价是投影画不出来（它在区域外），角也硬一些。

卡片挂在主窗口之下、当它的附属顶层窗（和设置窗同一套）：窗口管理器保证预览一直压在主窗口上面，
点主窗口也不会把画面盖住。实测这条关系没破「收进托盘预览该继续放着」—— 主窗口 hide() 之后卡片
原生仍然可见、仍然会重绘；只有真按标题栏最小化才跟着藏，还原时自己回来。
"""

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QPushButton,
                               QVBoxLayout, QWidget)

from xhs import preview
from xhs.ui import theme

PAD = 12            # 画面四周那圈边框：描边、圆角都靠它兜出来
RADIUS = 12         # 圆角半径，与确认框 / 设置页卡片同值
HEADER = 40         # 标题行高度：主播名 + ✕，按住这里可以拖动整张卡片
ASPECT = 9 / 16     # 竖屏直播的画幅，卡片整体按它给宽
EDGE = 40           # 卡片比主窗口上下各让出的一档，见 card_size

SETTLE_EVERY = 200    # 等 ffplay 把窗口开大、以及头几秒纠偏，多久问一次
ATTACH_TOTAL = 12000  # 等它自己改尺寸最多等多久（首帧一到就会改，实测半秒）
SETTLE_TOTAL = 3000   # 嵌进来之后还要按回原位几秒：ffplay 改完尺寸可能自己挪一回
WATCH_EVERY = 1000    # 进程自己退了（断流 -autoexit）就收掉卡片

_cards = {}           # user_id -> PreviewCard


def card_size(main_height: int) -> tuple:
    """高按主窗口算、上下各让出一档，宽跟着竖屏画幅

    让出那一档不是比例好看：卡片一旦顶到屏幕上下沿，四个角就落到屏幕外面去了，
    遮罩裁出来的圆角用户一个都看不见，只剩一个方方正正的大白块。
    """
    height = max(320, main_height - 2 * EDGE)
    return round(height * ASPECT), height


def content_rect(width: int, height: int, dpr: float) -> tuple:
    """画面该占的那块矩形（物理像素），原点相对卡片客户区左上角

    Win32 只认物理像素，Qt 给的是逻辑像素：200% 缩放时差一倍，不换算就是错位。
    画面四角正好落在圆角圆弧的圆心上，所以正方形的画面角不会被遮罩啃掉。
    """
    return (round(PAD * dpr),
            round((PAD + HEADER) * dpr),
            round((width - 2 * PAD) * dpr),
            round((height - 2 * PAD - HEADER) * dpr))


class PreviewCard(QWidget):
    """上半是标题栏（主播名 + ✕，按住可拖），下半是 ffplay 的原生窗口

    画面不是我们自己画的，所以 grab() 抓不到那块原生窗口 —— 自证得走真屏幕截图。
    """

    def __init__(self, user_info: dict, host: QWidget) -> None:
        # 把主窗口当 parent 传进去，配合下面的 Qt.Window 标志就成了「附属顶层窗」：
        # 窗口管理器保证它永远压在主窗口之上（和设置窗同一套），也不单独占任务栏
        super().__init__(host.window())
        self.user_id = str(user_info.get("user_id") or "")
        self.nickname = user_info.get("nickname") or self.user_id or "该主播"
        self.child_hwnd = 0

        # 不透明 + 遮罩裁圆角：逐像素透明（layered window）会让嵌进来的原生子窗口整个
        # 不画，所以这里不能学确认框那一套。
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        width, height = card_size(max(host.height(), 1))
        self.setFixedSize(width, height)
        self.setWindowTitle(f"预览 · {self.nickname}")
        self.move(_centered(host, width, height))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(PAD, PAD, PAD, PAD)
        outer.setSpacing(0)

        bar = QFrame(self)
        bar.setFixedHeight(HEADER)
        head = QHBoxLayout(bar)
        head.setContentsMargins(8, 0, 4, 0)
        head.setSpacing(6)
        caption = QLabel(self.nickname)     # 全名，不省略：卡片宽是算出来的，放得下
        caption.setObjectName("dialogTitle")
        caption.setToolTip(self.nickname)
        self.close_btn = QPushButton(theme.CLOSE_GLYPH)
        self.close_btn.setObjectName("iconClose")
        self.close_btn.setFixedSize(28, 28)
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.setToolTip("关闭预览")
        self.close_btn.clicked.connect(self.close)
        head.addWidget(caption)
        head.addStretch()
        head.addWidget(self.close_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        outer.addWidget(bar)
        outer.addStretch()      # 剩下整块都交给 ffplay

        self._poll = QTimer(self)           # 等 ffplay 把窗口建出来、再等它自己开大
        self._poll.setInterval(SETTLE_EVERY)
        self._poll.timeout.connect(self._attach)
        self._settle = QTimer(self)         # 嵌进来之后把画面按回内容区
        self._settle.setInterval(SETTLE_EVERY)
        self._settle.timeout.connect(self._reassert)
        self._watch = QTimer(self)          # 进程自己退了就把卡片收掉
        self._watch.setInterval(WATCH_EVERY)
        self._watch.timeout.connect(self._check_alive)
        self._settles_left = 0      # 还要把画面按回原位几次
        self._waits_left = 0        # 还允许等几轮它自己把窗口开大

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._apply_region()
        self._poll.start()
        self._watch.start()

    def _apply_region(self) -> None:
        """圆角靠窗口裁剪区域裁出来，而且必须按设备像素算

        不能改用 Qt 的 setMask：Qt 那份区域只有逻辑像素的精度，Windows 再放大一倍，
        台阶就是 2 物理像素一档、12 半径的圆弧只有 7 档，角上是一串点；描边又是
        QPainter 按设备像素反锯齿画的，两条路径对不上，多出来那半条被啃成断线。
        卡片本身还是拿不到反锯齿 —— 它必须是不透明窗口，逐像素透明（layered window）
        不合成 SetParent 进来的原生子窗口，嵌进来的画面会整个不画。
        """
        dpr = self.devicePixelRatioF()
        preview.set_round_region(int(self.winId()), round(self.width() * dpr),
                                 round(self.height() * dpr), round(RADIUS * dpr))

    def _attach(self) -> None:
        """两步走：先等它把窗口建出来，再等它自己把窗口开大，然后才认这张卡片当爹

        中间那一步不能省：ffplay 的窗口一出来是 SDL 默认的 640x480，解出首帧才自己改成
        -x/-y 那一档 —— 而它算画面摆在哪，正是按改完那一刻的尺寸来的。我们替它改，它的
        渲染视口还停在旧尺寸上，画面就放大着画出去、右边切掉一截（看着像没缩放、只露了
        一部分）。等的这半秒先把它藏起来，免得一个裸框在桌面上晃；真等过头（首帧就是慢）
        只能硬摆一挡，偏一点的画面胜过一张空卡片。
        """
        if not self.child_hwnd:
            hwnd = preview.find_hwnd(self.user_id)
            if not hwnd:
                return
            self.child_hwnd = hwnd
            self._waits_left = ATTACH_TOTAL // SETTLE_EVERY
            preview.set_visible(hwnd, False)
            return
        self._waits_left -= 1
        x, y, width, height = self.content_geometry()
        sized = preview.window_size(self.child_hwnd) == (width, height)
        if not sized and self._waits_left > 0:
            return
        self._poll.stop()
        if sized:
            preview.adopt(int(self.winId()), self.child_hwnd, x, y)
        else:
            preview.embed(int(self.winId()), self.child_hwnd, x, y, width, height)
        preview.set_visible(self.child_hwnd, True)
        # ffplay 改完尺寸还可能自己挪一回窗口，头几秒按着
        self._settles_left = SETTLE_TOTAL // SETTLE_EVERY
        self._settle.start()

    def _reassert(self) -> None:
        """把画面按回内容区左上角（只挪位），并把头几秒那几次用完为止"""
        if not self.child_hwnd:
            self._settle.stop()
            return
        x, y, _, _ = self.content_geometry()
        preview.move(self.child_hwnd, x, y)
        self._settles_left -= 1
        if self._settles_left <= 0:
            self._settle.stop()

    def _check_alive(self) -> None:
        if preview.is_open(self.user_id):
            return
        self.close()      # 断流后 ffplay 自己退了，卡片留在这就是一具空壳

    def content_geometry(self) -> tuple:
        return content_rect(self.width(), self.height(), self.devicePixelRatioF())

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), theme._qcolor("popup"))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._paint_matte(painter)
        shape = QPainterPath()
        shape.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5),
                             RADIUS, RADIUS)
        painter.setPen(QPen(theme._qcolor("overlay", 41), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(shape)
        painter.end()
        super().paintEvent(event)

    def _paint_matte(self, painter: QPainter) -> None:
        """画面那块先铺一层暗底：还没嵌进来时它是一块看得见的暗格，嵌好后被画面盖住"""
        x, y, width, height = self.content_geometry()
        dpr = self.devicePixelRatioF()
        painter.fillRect(QRect(round(x / dpr), round(y / dpr),
                               round(width / dpr), round(height / dpr)), QColor(16, 16, 18))

    def mousePressEvent(self, event) -> None:
        """按住标题行拖动整张卡片：系统标题栏去掉了，移动只能自己给"""
        if event.button() == Qt.MouseButton.LeftButton and self._in_header(event):
            handle = self.windowHandle()
            if handle is not None:
                handle.startSystemMove()
                return
        super().mousePressEvent(event)

    def _in_header(self, event) -> bool:
        # 事件从标题行里的子控件传上来时坐标是它们的局部坐标，一律换算回卡片再比
        local = self.mapFromGlobal(event.globalPosition().toPoint())
        return local.y() <= PAD + HEADER

    def closeEvent(self, event) -> None:
        for timer in (self._poll, self._settle, self._watch):
            timer.stop()
        if _cards.get(self.user_id) is self:
            _cards.pop(self.user_id, None)
        preview.close(self.user_id)     # 进程归进程，卡片归卡片，但只能一起走
        super().closeEvent(event)
        # 挂到主窗口下换来的是「压在主窗口之上」，代价是主窗口会把已关掉的卡片一直牵着；
        # 关掉就断开父子关系，卡片回到原来那样：没人引用它了自己就走
        self.setParent(None)


def _centered(host: QWidget, width: int, height: int) -> QPoint:
    """压在主窗口正中：预览就是来看这一路画面的，摆在边上等于让人扭头看

    主窗口收进托盘时没有几何可参照，退回屏幕可用区正中。越界只按可用区夹一次，
    上下各留的那一档（EDGE）已经保证最大化时四角在屏内。
    """
    screen = (host.screen() or QApplication.primaryScreen()).availableGeometry()
    area = host.geometry() if host.isVisible() else screen
    center = area.center()
    return QPoint(max(screen.left(), min(center.x() - width // 2, screen.right() - width + 1)),
                  max(screen.top(), min(center.y() - height // 2, screen.bottom() - height + 1)))


def open_card(user_info: dict, host: QWidget) -> str:
    """起一路预览并摆出卡片，回报一句给用户看的话（不抛异常，见 preview.open_preview）"""
    user_id = str(user_info.get("user_id") or "")
    existing = _cards.get(user_id)
    if existing is not None:
        existing.raise_()
        return f"[{existing.nickname}] 预览窗口已经开着了，已把它带到前面"

    width, height = card_size(max(host.height(), 1))
    _, _, content_w, content_h = content_rect(width, height, host.devicePixelRatioF())
    message = preview.open_preview(user_info, size=(content_w, content_h))
    if not preview.is_open(user_id):
        return message        # 进程没起来就别摆一张空卡片出去，那句话本身就是说明

    card = PreviewCard(user_info, host)
    _cards[user_id] = card
    card.show()
    card.raise_()
    return message


def close_card(user_id) -> bool:
    """关掉某个主播的预览（卡片连着进程一起）；返回是否真关了一路"""
    card = _cards.pop(str(user_id), None)
    closed = preview.close(user_id)
    if card is None:
        return closed
    card.close()
    return True


def close_all() -> int:
    """退出时收掉所有卡片：closeEvent 会顺带把进程一起 terminate"""
    count = len(_cards)
    for card in list(_cards.values()):
        card.close()
    return count + preview.close_all()


def is_open(user_id) -> bool:
    """该主播的预览卡片在不在：卡片是自己关的还是被 watchdog 收的，答案都一样"""
    return str(user_id) in _cards


def refresh_all() -> None:
    """换肤后重绘：底色和描边都是 paintEvent 现场取的，让它重画一次就够"""
    for card in _cards.values():
        card.update()

