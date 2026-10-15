"""通用控件与小部件：胶囊按钮、流式布局、图片缓存、圆角封面"""

import re
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from xhs import config
from xhs import net
from PySide6.QtCore import QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetrics, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPixmap, QRadialGradient)
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel,
                               QLayout, QMenu, QPushButton, QStyle, QStyledItemDelegate,
                               QVBoxLayout, QWidget)
from xhs.ui import theme


# 下拉弹出层参数：系统容器默认是一圈硬黑边 + 系统高亮色，与主题不搭，改为整层自绘
POPUP_ROW_HEIGHT = 24     # 单行高度
POPUP_ROW_PAD = 10        # 行内文字左右留白
POPUP_ROW_INSET = 3       # 高亮块左右内收，与描边之间留一圈底
POPUP_FONT_PIXEL = 12     # 行内字号：与 QSS 里 QComboBox 的 font-size 保持一致
POPUP_CHECK_SPACE = 12    # 行尾给对勾留的位：当前项画勾，文字不能压上去
# 单行文字之外的固定开销：高亮块左右内收 + 行内左右留白 + 行尾对勾位
POPUP_ROW_CHROME = 2 * POPUP_ROW_INSET + 2 * POPUP_ROW_PAD + POPUP_CHECK_SPACE
# 视图宽 → 行宽的最小兜底差值：首次弹出时滚动条可能还没占位，只量当前差值会算少
POPUP_VIEW_SPARE = 16


class _ConfirmDialog(QDialog):
    """圆角确认框：透明窗口里自绘卡片，投影是手画的几层浅黑影

    系统标题栏一去掉，圆角和投影都得自己来：
    - FramelessWindowHint 不能省。Windows 上只有它会让窗口真正带逐像素 alpha，否则
      圆角外那四个小方块和卡片外那圈空位直接上屏成黑（下拉弹层踩的是同一个坑）。
    - 系统投影在逐像素透明的窗上是贴着矩形右下的两条硬黑边，只能关掉自己画。
      这一档只能用复数形式的 setWindowFlags：setWindowFlag(NoDropShadow…) 设了不报错，
      但 windowFlags() 里根本没有那一位。
    """

    RADIUS = 12          # 卡片圆角：与设置页卡片同值
    SHADOW_PAD = 16      # 窗口比卡片多出的那圈，留给手画的投影
    CARD_PAD = 18        # 卡片自己的内边距：没有它，标题和按钮就贴在描边上
    HEAD_DRAG = 44       # 顶部这条高度内按住可拖动（标题行 + 一点余量）
    BUTTON_HEIGHT = 30   # 两枚按钮等高：样式表里 primaryBtn 不带 padding，不接管就一高一矮

    def __init__(self, parent, title: str, text: str, ok_text: str, cancel_text: str) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setMinimumWidth(380)

        outer = QVBoxLayout(self)
        pad = self.SHADOW_PAD + self.CARD_PAD
        outer.setContentsMargins(pad, pad, pad, pad)
        outer.setSpacing(0)

        head = QHBoxLayout()
        head.setSpacing(8)
        caption = QLabel(title)
        caption.setObjectName("dialogTitle")
        self.close_btn = QPushButton(theme.CLOSE_GLYPH)
        self.close_btn.setObjectName("iconClose")
        self.close_btn.setFixedSize(28, 28)
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.clicked.connect(self.reject)
        head.addWidget(caption)
        head.addStretch()
        head.addWidget(self.close_btn, 0, Qt.AlignmentFlag.AlignTop)

        body = QLabel(text)
        body.setObjectName("dialogBody")
        body.setWordWrap(True)

        foot = QHBoxLayout()
        foot.setSpacing(10)
        self.ok = QPushButton(ok_text)
        self.ok.setObjectName("primaryBtn")
        self.cancel = QPushButton(cancel_text)
        self.cancel.setObjectName("ghostBtn")
        self.ok.clicked.connect(self.accept)
        self.cancel.clicked.connect(self.reject)
        # 回车走主操作、Esc 走取消：QDialog 只白送 Esc，回车得自己指定默认按钮
        self.ok.setDefault(True)
        self.ok.setAutoDefault(True)
        for btn in (self.ok, self.cancel):
            # 全局样式表里 primaryBtn 没写 padding（别处都是调用方自己定尺寸），ghostBtn 写了
            # 6px 14px：放着不管，红底那枚会比描边那枚矮一截，五个字还挤到圆角上。
            # 控件自带的样式表压得过应用级的，这里按同名选择器写才顶得掉。
            btn.setFixedHeight(self.BUTTON_HEIGHT)
            btn.setStyleSheet(f"QPushButton#{btn.objectName()} {{ padding: 0px 14px; }}")
        foot.addStretch()
        foot.addWidget(self.ok)
        foot.addWidget(self.cancel)

        outer.addLayout(head)
        outer.addSpacing(12)
        outer.addWidget(body)
        outer.addSpacing(20)
        outer.addLayout(foot)

    def _card_rect(self) -> QRectF:
        pad = self.SHADOW_PAD + 0.5
        return QRectF(self.rect()).adjusted(pad, pad, -pad, -pad)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        card = self._card_rect()
        # 投影：几层同心的浅黑影往外铺，越外越淡，替掉系统那条硬边
        for step in range(4, 0, -1):
            blur = card.adjusted(-step * 1.6, -step * 1.2 + 2, step * 1.6, step * 1.6 + 2)
            path = QPainterPath()
            path.addRoundedRect(blur, self.RADIUS + step, self.RADIUS + step)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 9))
            painter.drawPath(path)
        path = QPainterPath()
        path.addRoundedRect(card, self.RADIUS, self.RADIUS)
        painter.setPen(QPen(theme._qcolor("overlay", 41), 1))
        painter.setBrush(theme._qcolor("popup"))
        painter.drawPath(path)
        painter.end()
        super().paintEvent(event)

    def mousePressEvent(self, event) -> None:
        handle = self.windowHandle()
        if event.button() == Qt.MouseButton.LeftButton and handle is not None \
                and event.position().y() < self.SHADOW_PAD + self.HEAD_DRAG:
            handle.startSystemMove()
            return
        super().mousePressEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._center_on_parent()

    def _center_on_parent(self) -> None:
        """摆在父窗正中：系统标题栏没了，Qt 那套默认摆放就不管事了

        父窗可能正最小化在托盘里（退出确认偏偏就是这种时候弹），拿不到可用几何时退回屏幕。
        """
        host = self.parentWidget()
        area = host.geometry() if host is not None and host.isVisible() else None
        if area is None:
            screen = self.screen() or QApplication.primaryScreen()
            area = screen.availableGeometry()
        self.move(area.center() - self.rect().center())


def confirm(parent, title: str, text: str, ok_text: str, cancel_text: str) -> bool:
    """主题化确认框：中文按钮 + 主界面那两套按钮样式（品牌红主操作 + 描边次操作）

    原来直接弹原生 QMessageBox：在全局「QWidget 透明底」下它渲染成黑底压深灰字，
    几乎看不清，按钮还是英文 Yes/No，跟程序其余部分不是一套东西。
    """
    box = _ConfirmDialog(parent, title, text, ok_text, cancel_text)
    return box.exec() == QDialog.DialogCode.Accepted


class _ComboPopupDelegate(QStyledItemDelegate):
    """弹出层行绘制：下拉用的是 QComboMenuDelegate，样式表的 ::item 规则它不认，只能自绘

    当前项 = 绿字 + 灰底 + 行尾绿勾；鼠标悬停只给灰底。
    早先用品牌红描高亮底，浅底上就成了红字压粉底，四字标签几乎读不出来。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_row = -1        # 由 ArrowComboBox 在弹出前灌入：视图自己不知道哪个是当前项

    def sizeHint(self, option, index):
        hint = super().sizeHint(option, index)
        return QSize(hint.width(), POPUP_ROW_HEIGHT)

    def paint(self, painter, option, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = painter.font()
        font.setPixelSize(POPUP_FONT_PIXEL)
        painter.setFont(font)
        current = index.row() == self.current_row
        rect = option.rect.adjusted(POPUP_ROW_INSET, 1, -POPUP_ROW_INSET, -1)
        if current or bool(option.state & (QStyle.StateFlag.State_Selected |
                                           QStyle.StateFlag.State_MouseOver)):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme._qcolor("overlay", 20))
            painter.drawRoundedRect(rect, 5, 5)
        text_rect = rect.adjusted(POPUP_ROW_PAD, 0, -(POPUP_ROW_PAD + POPUP_CHECK_SPACE), 0)
        painter.setPen(theme._qcolor("ok") if current else theme._qcolor("text", 218))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         str(index.data(Qt.ItemDataRole.DisplayRole) or ""))
        if current:
            self._paint_check(painter, rect)
        painter.restore()

    def _paint_check(self, painter, rect) -> None:
        """行尾绿勾：两笔圆头折线，位置跟着行的右内边距走"""
        pen = QPen(theme._qcolor("ok"))
        pen.setWidthF(1.7)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        x = float(rect.right()) - POPUP_ROW_PAD - POPUP_CHECK_SPACE + 1
        y = float(rect.center().y())
        painter.drawPolyline([QPointF(x, y - 0.4), QPointF(x + 3.2, y + 3.0),
                              QPointF(x + 8.4, y - 3.6)])


class ArrowComboBox(QComboBox):
    """下拉框：右侧小箭头自绘（样式表里用 border 拼三角会被 Qt 画成小黑块）

    可选「悬浮清除」：筛选类下拉选中了具体项后，鼠标移到箭头位置会换成 × ，
    点一下退回空值（placeholderText 那种灰字提示），不必展开菜单再挑「全部」。
    """

    ARROW_HALF_WIDTH = 3.5    # 箭头左右各延伸的宽度
    ARROW_HALF_HEIGHT = 1.8   # 箭头上沿到顶点的垂直距离
    ARROW_MARGIN = 11         # 箭头中心到右边缘的距离，与 ::drop-down 宽度居中对齐
    CLEAR_RADIUS = 7.0        # × 的圆形底半径，与箭头共用同一中心点
    COMBO_EDGE = 40           # 控件文字到边框的开销：QSS 内边距 6+6、drop-down 区 18、余量 10
    POPUP_EDGE = POPUP_ROW_CHROME + POPUP_VIEW_SPARE   # 弹层里同一行文字要的宽度
    POPUP_RADIUS = 8          # 弹层圆角：与设置页卡片、菜单那套一致
    POPUP_CARD_BORDER = 1     # 卡片外描边宽度：算弹层高度时要从视口里挖出来
    POPUP_V_MARGIN = 3        # 容器（透明）上下留白：卡片离按钮/界面边缘多远
    POPUP_ROW_GAP = 3         # 卡片描边到首末行的空位：这 3px 才是「行贴着圆角」的那道缝

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # 弹出层容器只当地图层，不画东西：QComboBoxPrivateContainer 是私有类，样式表打在
        # 它身上不生效，能生效的只有调色板铺底 —— 那是一律铺满的方角，圆角永远盖不住它。
        # 所以容器彻底透明，圆角卡片由视图自己画（QListView 是公开类，样式表照办）。
        box = self.view().parentWidget()
        box.setFrameShape(QFrame.Shape.NoFrame)
        box.setLineWidth(0)
        box.setAutoFillBackground(False)
        # 光设 WA_TranslucentBackground 不够：Windows 上这个 Qt::Popup 窗口仍按不透明建，
        # 圆角外那几像素不是透出界面而是涂黑，弹层上下边距和四角就成了黑影。
        # 只有再补上 FramelessWindowHint，窗口才真正带逐像素 alpha（拿纯绿底衬抓屏验证：
        # 只有这个 flag 会让边距处取回绿色；widget.grab() 两种情况都报 alpha 0，量不出来）。
        box.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        # 系统给弹层画的投影在逐像素透明的窗上是条硬边：贴着窗口矩形右下各一条
        # #8e8e8e，浅底上看着就是弹层多了两条黑线。卡片自己带描边，不缺这点层次。
        # 注意这一档只能用复数形式的 setWindowFlags：setWindowFlag(NoDropShadow…) 设了
        # 不报错，但 windowFlags() 里根本没有那一位（FramelessWindowHint 却没这问题）。
        box.setWindowFlags(box.windowFlags() | Qt.WindowType.NoDropShadowWindowHint)
        box.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        if box.layout() is not None:
            # 左右必须留 0：视图就是那张卡片，卡片宽度要刚好等于按钮宽度。
            # 上下给 POPUP_V_MARGIN：容器透明，这几 px 决定卡片离按钮多远（衬的是界面背景，
            # 不是卡片里的白底）。行贴着圆角那道缝不在这里，在视图的视口边距上。
            box.layout().setContentsMargins(0, self.POPUP_V_MARGIN, 0, self.POPUP_V_MARGIN)
        view = self.view()
        view.setFrameShape(QFrame.Shape.NoFrame)
        view.setAutoFillBackground(False)
        delegate = _ComboPopupDelegate(view)
        self._popup_delegate = delegate
        view.setItemDelegate(delegate)
        self._clearable = False
        self._hover_clear = False
        self.setMouseTracking(True)   # 悬浮出现 × 要收 mouseMove，不开追踪只有按住键才有
        self.currentIndexChanged.connect(self._sync_empty_state)
        self._sync_popup_style()

    def _sync_empty_state(self) -> None:
        """空值（框里只有 placeholder）时把文字压淡，否则和已选中的项长得一样"""
        empty = self.currentIndex() < 0
        if bool(self.property("placeholderShown")) == empty:
            return
        self.setProperty("placeholderShown", empty)
        # 动态属性参与选择器时，改完必须重新 polish，不然 QSS 规则不会重算
        self.style().unpolish(self)
        self.style().polish(self)

    def setCurrentIndex(self, index: int) -> None:
        super().setCurrentIndex(index)
        # 调用方批量改值时常把信号 block 掉，淡色状态不能只靠 currentIndexChanged
        self._sync_empty_state()

    def set_clearable(self, on: bool) -> None:
        """开启后：已选中具体项时，悬浮箭头处出现 × 可退回空值"""
        self._clearable = bool(on)
        if not self._clearable and self._hover_clear:
            self._hover_clear = False
            self.update()

    def _clear_rect(self) -> QRectF:
        center_x = self.width() - self.ARROW_MARGIN
        center_y = self.height() / 2.0
        return QRectF(center_x - self.CLEAR_RADIUS, center_y - self.CLEAR_RADIUS,
                      self.CLEAR_RADIUS * 2, self.CLEAR_RADIUS * 2)

    def _wants_clear(self, pos) -> bool:
        """× 只在「可清除 + 已选中具体项 + 鼠标压在箭头区 + 弹出层没开」时出现"""
        return (self._clearable and self.currentIndex() >= 0 and not self.view().isVisible()
                and self._clear_rect().contains(QPointF(pos)))

    def _sync_clear_hover(self, pos) -> None:
        want = self._wants_clear(pos)
        if want == self._hover_clear:
            return
        self._hover_clear = want
        self.update()

    def _sync_popup_style(self) -> None:
        """弹出层配色用控件级样式表：容器是私有类，应用级选择器与调色板在各平台都会被刷掉

        圆角卡片画在视图身上（容器只透明垫底），所以视图的边框就是弹层的外描边。
        """
        popup = theme._rgb("popup")
        edge = theme._rgba("overlay", 0.16)
        self.view().parentWidget().setStyleSheet("background: transparent;")
        self.view().setStyleSheet(
            f"QListView {{ background: {popup}; "
            f"border: {self.POPUP_CARD_BORDER}px solid {edge}; "
            f"border-radius: {self.POPUP_RADIUS}px; }}")

    def showPopup(self) -> None:
        self._sync_popup_style()      # 主题可能在两次弹出之间被切换
        self._popup_delegate.current_row = self.currentIndex()
        super().showPopup()
        self._fit_popup_height()      # 先收掉那条假滚动条，再量宽度
        self._fit_popup_width()

    def _fit_popup_height(self) -> None:
        """把弹层高度补成整数行

        Qt 按样式表默认行高算弹层高度，跟自绘委托的 POPUP_ROW_HEIGHT 对不上：两行的框
        只给到 43px（其实要 48px）。QListView 又是逐项滚动的，差的那点高度就等于整整
        一行，选中项落在下半截时第一行被滚出可视区 —— 看着像下拉少了一项。
        按委托报的真实行高把容器加满，行全都放得下，滚动条也不必出现（它一占就是 8px 宽）。
        """
        view = self.view()
        if self.count() > self.maxVisibleItems():
            # 项数超过可视行数，滚动条本来就该留着，别去动它
            view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            view.setMinimumHeight(0)
            return
        content = sum(view.sizeHintForRow(row) for row in range(self.count()))
        if content <= 0:
            return
        box = view.window()
        # 容器吃掉的高度按我们自己设的边框 + 布局留白算，不拿「容器高 - 视口高」去量：
        # 第二次弹出时 Qt 会把视图压回它自己那套高度，量到的就是那个中间态。
        layout = box.layout()
        top = bottom = 0
        if layout is not None:
            _, top, _, bottom = layout.getContentsMargins()
        chrome = 2 * box.frameWidth() + top + bottom + 2 * self.POPUP_CARD_BORDER
        view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # 跟横向一个道理：光 resize 容器，容器里的布局不会重跑，视图还是 Qt 给的那点高。
        # 把下限钉在视图身上，布局什么时候跑、跑几次都得把它撑开。下限里带上卡片自身那圈
        # 边框：样式表的 border 是从视口里挖的，不算进去末行就压在边框上。
        # 再加上下 POPUP_ROW_GAP：卡片描边到首末行之间那点白底，实测只有 1px（行自己
        # 内收的那 1px），两行就是贴在圆角上的。
        gap = self.POPUP_ROW_GAP
        view.setMinimumHeight(content + 2 * self.POPUP_CARD_BORDER + 2 * gap)
        box.resize(box.width(), content + chrome + 2 * gap)
        # 视图只有 1 行时，Qt 仍留着它当初替滚动条划出的 6px 下边距：视图 24 而视口 18，
        # 唯一那行的胶囊底边就压在边缘上。边距不让出来，视图撑多高都不关视口的事。
        view.setViewportMargins(0, gap, 0, gap)
        view.verticalScrollBar().setValue(0)   # 全部行都放得下，正确偏移就是 0

    def _fit_popup_width(self) -> None:
        """弹层与按钮同宽：Qt 默认按内容把弹层撑得比按钮宽，两块错位很难看

        反向钉死成控件宽度是安全的：控件的 minimumWidth 由 contents_fit_width()
        兜住最宽的一项（那一档把弹层行宽也算在内），所以同宽不会裁字。
        容器吃掉多少宽度按它自己的边框 + 布局留白算，不拿「容器宽 - 视图宽」去量：
        那一刻视图还没重排，量到的可能是它上一次的高度态宽度。
        """
        view = self.view()
        box = view.window()
        layout = box.layout()
        left = right = 0
        if layout is not None:
            left, _, right, _ = layout.getContentsMargins()
        chrome = 2 * box.frameWidth() + left + right
        wanted = self.width()
        if wanted <= 0:
            return
        view.setMinimumWidth(max(1, wanted - chrome))
        box.resize(wanted, box.height())
        if layout is not None:
            layout.activate()

    def _widest_item_text(self) -> int:
        font = QFont(self.font())
        font.setPixelSize(POPUP_FONT_PIXEL)   # 与 QSS 里 QComboBox 的 font-size 一致
        metrics = QFontMetrics(font)
        return max((metrics.horizontalAdvance(self.itemText(row))
                    for row in range(self.count())), default=0)

    def contents_fit_width(self) -> int:
        """控件的最小宽度：既容得下框内文字，也让弹层里同一行文字完整显示

        只有一项的弹层不会跟着加宽（Qt 认为没必要），所以这一档只能靠控件宽度兜住；
        截成「最近录制」也就看不出排的是哪一列。
        """
        return self._widest_item_text() + max(self.COMBO_EDGE, self.POPUP_EDGE)

    def mouseMoveEvent(self, event) -> None:
        self._sync_clear_hover(event.position().toPoint())
        super().mouseMoveEvent(event)

    def enterEvent(self, event) -> None:
        # 进来就停在箭头上方时，不会有 mouseMove，这里补一次判定
        self._sync_clear_hover(event.position().toPoint())
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if self._hover_clear:
            self._hover_clear = False
            self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if (event.button() == Qt.MouseButton.LeftButton
                and self._wants_clear(event.position().toPoint())):
            event.accept()     # 吞掉按下：不让下拉弹出，清除动作交给 release
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if (event.button() == Qt.MouseButton.LeftButton
                and self._wants_clear(event.position().toPoint())):
            event.accept()
            self.setCurrentIndex(-1)   # 回到空值：触发 currentIndexChanged，筛选即时生效
            self._sync_clear_hover(event.position().toPoint())
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._hover_clear:
            self._paint_clear(painter)
            painter.end()
            return
        pen = QPen(theme._qcolor("text", 155))
        pen.setWidthF(1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        center_x = self.width() - self.ARROW_MARGIN
        center_y = self.height() / 2.0
        painter.drawPolyline([
            QPointF(center_x - self.ARROW_HALF_WIDTH, center_y - self.ARROW_HALF_HEIGHT),
            QPointF(center_x, center_y + self.ARROW_HALF_HEIGHT),
            QPointF(center_x + self.ARROW_HALF_WIDTH, center_y - self.ARROW_HALF_HEIGHT),
        ])
        painter.end()

    def _paint_clear(self, painter: QPainter) -> None:
        """灰圆 + 反色 × ：与参考样式一致，× 用弹层底色描出，避免糊成一团"""
        box = self._clear_rect()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme._qcolor("text", 130))
        painter.drawEllipse(box)
        pen = QPen(theme._qcolor("popup"))
        pen.setWidthF(1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        center = box.center()
        reach = 3.1
        painter.drawLine(QPointF(center.x() - reach, center.y() - reach),
                         QPointF(center.x() + reach, center.y() + reach))
        painter.drawLine(QPointF(center.x() - reach, center.y() + reach),
                         QPointF(center.x() + reach, center.y() - reach))


# 菜单卡片：整窗透明 + 自绘，样式表只留 padding（QSS 的底是铺满整个窗口矩形的）
MENU_RADIUS = 8         # 卡片圆角：与下拉弹层那张卡片同一档
MENU_EDGE = 1           # 卡片描边宽度
MENU_PAD = 6            # 卡片内边距：与 QSS 里 QMenu 的 padding 对齐
# 「···」菜单多出来的气泡尖角：卡片圆角外得真透出底下内容，尖角才凸得出来
CALLOUT_ARROW = 7       # 尖角凸出卡片多宽：窗口矩形比可见卡片宽这么多
CALLOUT_HALF = 6.0      # 尖角底边的一半高
CALLOUT_GAP = 4         # 尖角与锚点按钮之间的空隙
CALLOUT_HEAD = 18       # 尖角默认离窗口顶多远：让菜单往下长，尖角落在卡片上沿附近


class RoundedMenu(QMenu):
    """圆角菜单：托盘菜单、「显示字段」面板这类不带尖角的弹出菜单

    样式表里那句 QMenu{border-radius} 从来没生效过：QSS 的底是铺满整个窗口矩形的，
    而 Qt::Popup 默认按不透明建窗，四个角于上是实心的。配方跟下拉弹层一样 ——
    FramelessWindowHint 才有逐像素 alpha，系统投影在透明窗上是贴着矩形右下的硬黑边，
    只能关掉，靠自绘那 1px 描边把卡片从背景里分出来。
    """

    OBJECT_NAME = "roundedMenu"
    RADIUS = MENU_RADIUS
    EDGE = MENU_EDGE
    PAD = MENU_PAD

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName(self.OBJECT_NAME)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet(f"QMenu#{self.OBJECT_NAME} {{ background: transparent; "
                           f"border: none; padding: {self.PAD}px; }}")

    def _card_rect(self) -> QRectF:
        # 往里收半个描边宽，1px 的线才不会压在窗口边上被裁掉半条
        half = self.EDGE / 2.0
        return QRectF(self.rect()).adjusted(half, half, -half, -half)

    def _paint_card(self, painter: QPainter, shape: QPainterPath) -> None:
        painter.fillPath(shape, theme._qcolor("popup"))
        painter.setPen(QPen(theme._qcolor("overlay", 41), self.EDGE))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(shape)

    def _card_shape(self) -> QPainterPath:
        """要涂成卡片的那块形状：不带尖角就是一张圆角矩形"""
        path = QPainterPath()
        path.addRoundedRect(self._card_rect(), self.RADIUS, self.RADIUS)
        return path

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._paint_card(painter, self._card_shape())
        painter.end()
        super().paintEvent(event)      # 菜单项照常画，压在卡片上


class CalloutMenu(RoundedMenu):
    """带气泡尖角的菜单：尖角指着「···」，卡片和尖角一起自绘

    尖角想凸出卡片就得让窗口比卡片宽出一条空位，那条空位又必须是真透明 ——
    所以卡片和尖角由 paintEvent 画，QSS 只留 padding。
    """

    OBJECT_NAME = "calloutMenu"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # 一次性的菜单：popup 不阻塞，关掉就该收掉，别挂在视图下越攒越多
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._arrow_y = float(CALLOUT_HEAD)
        self._point_left = True
        self._sync_side_style()

    def set_side(self, point_left: bool) -> None:
        """point_left = 尖角在卡片左边（菜单开在锚点右侧）；换边时那条空位也跟着换边"""
        self._point_left = bool(point_left)
        self._sync_side_style()

    def _sync_side_style(self) -> None:
        near = self.PAD + CALLOUT_ARROW
        pad = (f"{self.PAD}px {self.PAD}px {self.PAD}px {near}px" if self._point_left
               else f"{self.PAD}px {near}px {self.PAD}px {self.PAD}px")
        self.setStyleSheet(
            f"QMenu#{self.objectName()} {{ background: transparent; border: none; "
            f"padding: {pad}; }}")

    def set_arrow_y(self, y: float) -> None:
        self._arrow_y = float(y)

    def points_left(self) -> bool:
        return self._point_left

    def arrow_y(self) -> float:
        return self._arrow_y

    def _card_shape(self) -> QPainterPath:
        """卡片 + 尖角合成一块：接缝处若即若离，描边就会在中间断成一条亮线"""
        # 尖角那一侧让出 CALLOUT_ARROW 那条空位，卡片只剩另一边
        left = CALLOUT_ARROW if self._point_left else self.EDGE / 2.0
        card = QRectF(left, self.EDGE / 2.0,
                      self.width() - CALLOUT_ARROW - self.EDGE, self.height() - self.EDGE)
        shape = QPainterPath()
        shape.addRoundedRect(card, self.RADIUS, self.RADIUS)
        # 底边往卡片里咬半个描边，尖角与卡片接缝处不留亮线
        base = card.left() - self.EDGE / 2.0 if self._point_left \
            else card.right() + self.EDGE / 2.0
        tip = self.EDGE / 2.0 if self._point_left else self.width() - self.EDGE / 2.0
        tri = QPainterPath()
        tri.moveTo(base, self._arrow_y - CALLOUT_HALF)
        tri.lineTo(tip, self._arrow_y)
        tri.lineTo(base, self._arrow_y + CALLOUT_HALF)
        tri.closeSubpath()
        return shape.united(tri)


def show_callout_menu(menu: CalloutMenu, anchor: QWidget) -> None:
    """把菜单摆到锚点旁边，尖角指着锚点中线；右边放不下就翻到左边开

    锚点是「···」那枚小按钮：卡片/列表行都靠右，光往右开的话菜单常常糊到屏幕外。
    """
    menu.set_side(True)
    menu.ensurePolished()          # padding 进了 sizeHint，宽度才算得准
    size = menu.sizeHint()
    box = anchor.mapToGlobal(QPoint(0, 0))
    area = anchor.screen().availableGeometry() if anchor.screen() else \
        QApplication.primaryScreen().availableGeometry()
    x = box.x() + anchor.width() + CALLOUT_GAP
    if x + size.width() - 1 > area.right():
        menu.set_side(False)
        menu.ensurePolished()
        size = menu.sizeHint()
        x = box.x() - CALLOUT_GAP - size.width()
    center_y = box.y() + anchor.height() // 2
    y = center_y - CALLOUT_HEAD
    y = max(area.top(), min(y, area.bottom() - size.height() + 1))
    x = max(area.left(), min(x, area.right() - size.width() + 1))
    # 窗口被上下夹过时锚点可能已经不在菜单范围内，尖角最后再夹进卡片的两圆角之间
    menu.set_arrow_y(min(max(center_y - y, MENU_RADIUS + 4),
                         size.height() - MENU_RADIUS - 4))
    menu.popup(QPoint(x, y))


class HeadLabel(QLabel):
    """可点击的表头标签：列表按列排序的入口，箭头由文字后缀表达"""

    clicked = Signal()

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event) -> None:
        if (event.button() == Qt.MouseButton.LeftButton
                and self.rect().contains(event.position().toPoint())):
            event.accept()
            self.clicked.emit()
            return
        super().mouseReleaseEvent(event)

# ============================================================
# 关注列表视图：卡片 / 列表 双显示
# ============================================================
# 平台名统一使用模块级 PLATFORM_NAME（保存路径中的 {platform} 与之保持一致）
_image_cache = None
IMAGE_CACHE_MAX = 400    # 裁切图上限：单张只有控件大小，可以多存
IMAGE_SOURCE_MAX = 150   # 原图上限：w/540 原图解码后约 1.2MB/张，不能跟裁切图共用上限
HI_RES_WIDTH = 540       # 头像 CDN 尺寸参数改写值：120px 小图拉到封面必糊

def _hi_res_url(url: str) -> str:
    """把头像链接里 imageView2 的尺寸参数改写成 w/540，取回高分辨率原图

    没有该参数的链接原样返回；CDN 对不足 540 宽的原图返回原尺寸，不会反向放大。
    """
    return re.sub(r"(imageView2[^#&]*?)w/\d+", rf"\1w/{HI_RES_WIDTH}", url, count=1)

def get_image_cache():
    """惰性创建网络图片缓存（首次调用必须在 GUI 线程）"""
    global _image_cache
    if _image_cache is None:
        _image_cache = ImageCache()
    return _image_cache

STATE_PILL_WIDTH = 56   # 状态类胶囊统一最小宽度：「空闲」与「录制中」等宽，避免行内跳变


def _pill(text: str, color: str, background: str, min_width: int = 0) -> QLabel:
    """小圆角标签（平台 / 直播状态）"""
    label = QLabel(text)
    theme._pill_style(label, text, color, background, min_width)
    return label

def _apply_platform_pill(label: QLabel) -> None:
    """平台标签着色：两套主题下都取各自的蓝色，切主题时按新配色重套一次即可"""
    theme._pill_style(label, config.PLATFORM_NAME, theme._rgb("link"), theme._rgba("link", 0.16))

def _platform_pill() -> QLabel:
    """平台标签：新建即着好当前主题的颜色"""
    label = QLabel()
    _apply_platform_pill(label)
    return label

def _apply_state_pill(label: QLabel, is_live: bool) -> None:
    """就地刷新直播状态胶囊：开播 = 绿色，未开播 = 中性灰（随主题变化）"""
    if is_live:
        theme._pill_style(label, "直播中", theme._rgb("ok"), theme._rgba("ok", 0.16), STATE_PILL_WIDTH)
    else:
        theme._pill_style(label, "未开播", theme._rgba("text", 0.55), theme._rgba("overlay", 0.07), STATE_PILL_WIDTH)

def _state_pill(user: dict) -> QLabel:
    """直播状态标签"""
    label = QLabel()
    _apply_state_pill(label, bool(user.get("is_live")))
    return label

class AutoRecordTag(QWidget):
    """自动录制标签：透明底的细环「24」，只在开了 24 小时自动录制时挂出来

    纯状态说明，不可点：改状态走卡片右下角「···」菜单。原来它是枚能按的开关，
    但同一张卡片上「能按的东西」越多，误触就越多，而关掉它以后环内浓淡两档
    也失去了意义——不显示就是关，显示就是开。
    全灰阶：这两枚挨着昵称，任何彩色都会先于文字抢到注意力。
    环内不铺底：卡片本身是半透明胶囊，再压一层底色就把封面图和卡片底纹糊掉了。
    尺寸按昵称的实际墨迹高定：12px 加粗中文笔画实占 11-12px（不是 15 的行高），
    所以环外沿收到 12px —— 描边 1.4 是画在半径上的，直径就得 10.6。原来外沿 16.4，
    比字高出一圈，昵称行里它比名字还抢眼。
    画在设备无关坐标上，由 Qt 按屏幕 DPR 放大后备缓冲，所以任何缩放比例下都是正圆、
    边缘都带抗锯齿；配色每次重绘现取主题，换主题只要 update() 一下就跟上。
    """

    SIZE = 12              # 控件边长：与环外沿同值，不留富余
    DIAMETER = 10.6        # 正圆直径：加 1.4 描边正好 12，跟昵称的墨迹同高
    TEXT_PIXEL = 7         # 环内「24」的像素字号：环里只剩这点地方放字
    INK_ALPHA = 205        # 灰阶浓淡：只有一档，开 / 关由显示与否表达

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setAccessibleName("自动录制：已开启")

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = QPointF(self.width() / 2.0, self.height() / 2.0)
        radius = self.DIAMETER / 2.0
        accent = theme._qcolor("text", self.INK_ALPHA)

        painter.setPen(QPen(accent, 1.4))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(center, radius, radius)

        font = painter.font()
        font.setPixelSize(self.TEXT_PIXEL)
        painter.setFont(font)
        painter.setPen(accent)
        painter.drawText(QRectF(self.rect()), Qt.AlignmentFlag.AlignCenter, "24")
        painter.end()


class LiveBadge(QWidget):
    """直播标识：裸广播形 ((·)) glyph，不套圆环，只在开播时显示（只读，不可点）

    圆环留给右边那枚「24」：两枚都带环时读者分不清哪个是标签哪个是状态，
    而这个符号只是开播说明。灰阶同 AutoRecordTag，靠浓淡而不是颜色表意。
    大小也照「24」环那把尺：外沿墨迹 = 2*(外弧 5.6 + 半个描边) ≈ 12.4，与昵称 12px
    加粗的实占墨迹同高。原来是 16（格子 20），比字高出一圈，昵称行里比名字还抢眼。
    """

    SIZE = 14              # 控件格子：比墨迹宽 1.6，给抗锯齿留点边
    DOT_RADIUS = 1.6
    ARC_RADII = (3.4, 5.6)
    ARC_PEN = 1.2          # 两道弧间距只剩 2.2，描边再粗一点它们就要糊成一道
    ARC_HALF_ANGLE = 40    # 每道弧以水平方向为中心上下各张开的度数

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setToolTip("正在直播")

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = QPointF(self.width() / 2.0, self.height() / 2.0)
        color = theme._qcolor("text", 205)

        # 广播 glyph：中心实心点 + 左右各两道弧（drawArc 角度单位是 1/16 度）
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(color))
        painter.drawEllipse(center, self.DOT_RADIUS, self.DOT_RADIUS)
        painter.setPen(QPen(color, self.ARC_PEN))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        half = self.ARC_HALF_ANGLE * 16
        for arc_radius in self.ARC_RADII:
            rect = QRectF(center.x() - arc_radius, center.y() - arc_radius,
                          arc_radius * 2, arc_radius * 2)
            painter.drawArc(rect, -half, half * 2)        # 右侧弧：以 0° 为中心
            painter.drawArc(rect, 180 * 16 - half, half * 2)   # 左侧弧：以 180° 为中心
        painter.end()

def _cover_pixmap(pixmap: QPixmap, width: int, height: int) -> QPixmap:
    """等比裁切填充：先放大到铺满目标尺寸，再居中裁剪"""
    if pixmap is None or pixmap.isNull() or width <= 0 or height <= 0:
        return pixmap
    scaled = pixmap.scaled(width, height, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                           Qt.TransformationMode.SmoothTransformation)
    return scaled.copy(max(0, (scaled.width() - width) // 2),
                       max(0, (scaled.height() - height) // 2), width, height)

class FlowLayout(QLayout):
    """流式布局：窗口缩放时卡片列数自适应"""

    def __init__(self, parent=None, margin: int = 0, spacing: int = 10):
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _do_layout(self, rect, test_only: bool) -> int:
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y, line_height = area.x(), area.y(), 0
        spacing = self.spacing()
        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + spacing
            if next_x - spacing > area.right() and line_height > 0:
                x = area.x()
                y += line_height + spacing
                next_x = x + hint.width() + spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + margins.bottom()

class FlowContainer(QWidget):
    """流式布局容器：把 heightForWidth 结果同步到最小高度

    注意：QScrollArea 不会处理 heightForWidth，必须显式同步最小高度，
    否则卡片会被裁切且滚动条不出现。
    """

    def __init__(self, margin: int = 0, spacing: int = 10, parent=None):
        super().__init__(parent)
        self.flow = FlowLayout(self, margin=margin, spacing=spacing)

    def sync_height(self) -> None:
        width = self.width()
        if width <= 0:
            return
        height = self.flow.heightForWidth(width)
        if height > 0 and height != self.minimumHeight():
            self.setMinimumHeight(height)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.sync_height()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.sync_height()

class ImageCache(QObject):
    """网络图片缓存：线程池下载，回到 GUI 线程后再构建 QPixmap"""

    loaded = Signal(str, bytes)   # url, data（工作线程发出）
    updated = Signal(str)         # url（GUI 线程发出，通知重绘）
    FAILED_COOLDOWN = 120.0       # 下载失败的冷却时间：过后再试，不永久黑掉

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmaps = OrderedDict()   # url -> 原图，按访问顺序做 LRU（只在 GUI 线程访问）
        self._shaped = OrderedDict()    # (url, w, h) -> 裁好的图，避免每次重绘重新缩放
        self._pending = set()
        self._failed = {}               # url -> 失败时间戳（池线程与 GUI 线程都会碰，加锁）
        self._state_lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="img")
        self.loaded.connect(self._store)

    def pixmap(self, url):
        if not url:
            return None
        pixmap = self._pixmaps.get(url)
        if pixmap is not None:
            self._pixmaps.move_to_end(url)
        return pixmap

    def shaped(self, url: str, width: int, height: int, dpr: float = 1.0):
        """取按目标物理尺寸裁好的图：控件尺寸固定，缩放结果可长期复用

        滚动 / 刷新会触发大量重绘，若每次 paintEvent 都做一遍 SmoothTransformation
        缩放 + 复制，几百张头像足以把 GUI 线程拖住（缩放大图是主要开销）。
        按 dpr 生成物理像素并写回 devicePixelRatio，高缩放屏绘制时不再二次放大。
        """
        pixmap = self.pixmap(url)
        if pixmap is None or pixmap.isNull() or width <= 0 or height <= 0:
            return None
        phys_w, phys_h = max(1, round(width * dpr)), max(1, round(height * dpr))
        key = (url, phys_w, phys_h)
        cached = self._shaped.get(key)
        if cached is None:
            cached = _cover_pixmap(pixmap, phys_w, phys_h)
            cached.setDevicePixelRatio(dpr)
            self._shaped[key] = cached
            if len(self._shaped) > IMAGE_CACHE_MAX:
                self._shaped.popitem(last=False)
        else:
            self._shaped.move_to_end(key)
        return cached

    def request(self, url: str) -> None:
        if not url or url in self._pixmaps:
            return
        with self._state_lock:
            failed_at = self._failed.get(url)
            # 失败只冷却一段时间：网络抖一次就把这个头像永久黑掉，重启前都补不回来
            if failed_at is not None and time.time() - failed_at < self.FAILED_COOLDOWN:
                return
            if url in self._pending:
                return
            self._failed.pop(url, None)
            self._pending.add(url)
        self._pool.submit(self._download, url, _hi_res_url(url))

    def _download(self, url: str, fetch: str) -> None:
        data = self._fetch(fetch)
        if not data and fetch != url:
            data = self._fetch(url)   # 大图参数不被接受时回退原链接，最坏等同改动前
        if not data:
            with self._state_lock:
                self._pending.discard(url)
                self._failed[url] = time.time()
            return
        self.loaded.emit(url, data)   # bytes 跨线程传递安全

    @staticmethod
    def _fetch(url: str) -> bytes:
        try:
            response = net._session().get(url, timeout=15)
            if response.status_code == 200:
                return response.content
        except Exception:
            return b""
        return b""

    def _store(self, url: str, data: bytes) -> None:
        with self._state_lock:
            self._pending.discard(url)
        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            with self._state_lock:
                self._failed[url] = time.time()
            return
        self._pixmaps[url] = pixmap
        # 超出容量淘汰最久未用的：整体 clear 会让缓存里的图重新走一遍网络下载
        if len(self._pixmaps) > IMAGE_SOURCE_MAX:
            self._pixmaps.popitem(last=False)
        self.updated.emit(url)

class RoundedImage(QWidget):
    """圆角图片控件：cover 裁切 + 缺图时渐变占位（封面与头像共用）"""

    def __init__(self, width: int, height: int, radius: int = 6, corners: str = "all",
                 seed: str = "", placeholder: str = "", parent=None):
        super().__init__(parent)
        self.setFixedSize(width, height)
        self._radius = radius
        self._corners = corners
        self._seed = seed or "xhs"
        self._placeholder = placeholder
        self._url = ""

    def set_url(self, url: str, seed: str = "", placeholder: str = "") -> None:
        self._url = url or ""
        if seed:
            self._seed = seed
        if placeholder:
            self._placeholder = placeholder
        if self._url:
            get_image_cache().request(self._url)
        self.update()

    @property
    def url(self) -> str:
        return self._url

    def refresh(self, url: str = "") -> None:
        """重绘；传入 url 时只在该图片属于自己时才重绘"""
        if url and url != self._url:
            return
        self.update()

    def _shape(self) -> QPainterPath:
        area = QRectF(self.rect())
        path = QPainterPath()
        if self._corners == "top":   # 只圆上方两角，下方与卡片贴合
            # 圆角矩形向下加长一个 radius：底部两角的圆弧落在控件裁剪区外，
            # 视觉上就是「上圆下方」。不要再叠加第二个矩形取并集——
            # 填充没问题，但 drawPath 描边会把子路径的公共边也画出来，
            # 封面正中间就多了一条横线。
            path.addRoundedRect(area.adjusted(0, 0, 0, self._radius), self._radius, self._radius)
        else:
            path.addRoundedRect(area, self._radius, self._radius)
        return path

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        path = self._shape()
        painter.setClipPath(path)

        pixmap = (get_image_cache().shaped(self._url, self.width(), self.height(),
                                           self.devicePixelRatioF())
                  if self._url else None)
        if pixmap is not None:
            painter.drawPixmap(QPoint(), pixmap)   # 物理像素 1:1 落位，dpr 已写进 pixmap
        else:
            first, second = theme._seed_colors(self._seed)
            gradient = QLinearGradient(0, 0, self.width(), self.height())
            gradient.setColorAt(0.0, first)
            gradient.setColorAt(1.0, second)
            painter.fillPath(path, QBrush(gradient))
            if self._placeholder:
                font = QFont(self.font())
                font.setPointSize(max(9, int(min(self.width(), self.height()) * 0.34)))
                font.setBold(True)
                painter.setFont(font)
                painter.setPen(QPen(QColor(255, 255, 255, 240)))
                painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._placeholder)

        painter.setClipping(False)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(theme._qcolor("overlay", 20), 1.0))
        painter.drawPath(path)
        painter.end()

def _with_alpha(color: QColor, alpha: int) -> QColor:
    """换个浓度，别的照抄：径向渐变的每一档色标都要一份"""
    tinted = QColor(color)
    tinted.setAlpha(max(0, min(255, int(alpha))))
    return tinted


class CoverBox(RoundedImage):
    """卡片封面：16:9 画面 + 左上角直播标题 + 底部状态条（直播/录制状态 · 画质 · 时长）"""

    BAR_HEIGHT = 22

    # 在录 / 已开播这两档点大一号：录制中再带扩散圈，未开播只是个小灰点
    DOT_ACTIVE = 10.0
    DOT_IDLE = 7.0
    DOT_CX = 13.0          # 圆心横坐标：三种状态共用，切状态时点不会左右跳
    TEXT_X = 26            # 状态文字左边界：按最大那颗点留位，录制中 / 直播中才对齐
    # 不画点的那一档文字顶到点原本占的那格左边沿（圆心 13 - 灰点半径 3.5），
    # 沿用 TEXT_X 会在状态条左侧空出一截，像是漏了个控件
    TEXT_X_BARE = DOT_CX - DOT_IDLE / 2.0
    # 虚影外沿从点边向外走多远：点半径 5 + 这段要 ≤ 状态条半高 11，再大最外圈就会被
    # 条子上下沿切平，看着像画坏了
    RIPPLE_SPAN = 6.0
    RIPPLE_PEAK = 165      # 贴着点边那一档的最亮浓度
    RIPPLE_FADE = 0.55     # 走完这一段才开始淡出：前面全力摊开，最后四成半散干净
    RIPPLE_PERIOD = 1.6    # 一道波纹从红点走到散掉的时间（秒）：原来 2.4 一圈，慢到像没在动。
                           # 1.6 秒配 40ms 重绘仍有 40 帧可走，摊开的过程不会看着跳格
    RIPPLE_TICK = 40       # 重绘间隔：25fps 够顺，也不跟主线程抢别的活

    # 左上角直播标题胶囊：边距取 8 与封面顶部圆角同值，文字不会被圆角切到
    PILL_MARGIN = 8
    PILL_HEIGHT = 20
    PILL_PAD = 8
    PILL_FONT_PIXEL = 11

    def __init__(self, user: dict, width: int, height: int, parent=None):
        super().__init__(width=width, height=height, radius=8, corners="top",
                         seed=user.get("user_id", ""),
                         placeholder=(user.get("nickname") or "?")[:1], parent=parent)
        self._user = dict(user)
        self._record = None
        self._ripple = QTimer(self)
        self._ripple.setInterval(self.RIPPLE_TICK)
        self._ripple.timeout.connect(self._advance_ripple)

    def sync_user(self, user: dict) -> None:
        """复用控件时只刷新用户相关数据（封面图 / 画质 / 最近录制）"""
        self._user = dict(user)
        self.set_url(user.get("avatar", ""), placeholder=(user.get("nickname") or "?")[:1])

    def set_record(self, stats) -> None:
        self._record = stats
        self._sync_ripple()
        self.update()

    def set_last_record(self, text: str) -> None:
        self._user["last_record"] = text
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        bar = QRectF(0, self.height() - self.BAR_HEIGHT, self.width(), self.BAR_HEIGHT)
        painter.fillRect(bar, QColor(0, 0, 0, 150))
        self._paint_live_title(painter)

        quality = self._user.get("quality") or ""
        # 灰点只留给「压根没录过」的卡：有最近录制时间就说明这主播录过了，
        # 状态条上那行时间本身就是结论，再顶个点纯属噪音
        show_dot = True
        if self._record:
            dot_color, state_text = QColor("#FF2442"), "录制中"
            if quality:
                state_text = f"{state_text} {quality}"
            right_text = self._record.get("elapsed") or ""
            short_text = state_text
        elif self._user.get("is_live"):
            # 开播待录用平台蓝：跟录制中的品牌红区分，绿色留给徽章那类「正常」语义
            dot_color, state_text = theme._qcolor("link"), "直播中"
            if quality:
                state_text = f"{state_text} {quality}"
            right_text = "等待录制"
            short_text = state_text
        else:
            # 未开播这一档状态条没别的要占，直接写最近录制时间。灰点 + 这行字
            # 一起把「没在播」说清楚，所以「未开播」三个字可以让位
            dot_color = QColor(255, 255, 255, 90)
            last_record = (self._user.get("last_record") or "").strip()
            state_text = f"最近录制时间：{last_record}" if last_record else "未开播"
            short_text = last_record or state_text
            right_text = ""
            show_dot = not last_record

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(dot_color))
        center = QPointF(self.DOT_CX, bar.center().y())
        if self._record:
            self._paint_ripple(painter, bar, center, dot_color)
        if show_dot:
            size = self.DOT_ACTIVE if self._record or self._user.get("is_live") else self.DOT_IDLE
            painter.drawEllipse(center, size / 2.0, size / 2.0)

        font = QFont(self.font())
        font.setPointSize(8)
        font.setBold(True)
        painter.setFont(font)
        text_x = self.TEXT_X if show_dot else self.TEXT_X_BARE
        text_area = QRectF(text_x, bar.top(), self.width() - text_x - 8, self.BAR_HEIGHT)
        if painter.fontMetrics().horizontalAdvance(state_text) > text_area.width():
            # 窄卡片放不下带标签的整串：标签先让位，时间戳本身绝不切一半（切了就看不出年份）
            state_text = short_text
        painter.setPen(QPen(QColor(255, 255, 255, 240)))
        painter.drawText(text_area, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, state_text)
        if right_text:
            painter.setPen(QPen(QColor(255, 255, 255, 205)))
            painter.drawText(text_area, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, right_text)
        painter.end()

    def cover_caption(self) -> str:
        """封面左上角那行字：只认直播标题，主播没填就留空

        试过退回个人简介首行（`fa51f39`），当天就改回来了：简介答的是「这个人是谁」，
        标题答的是「这场在播什么」，同一格混着读会认错；而且简介不随场次变，每场都是
        同一行，看着像卡住了。空着至少没说错话。
        """
        if not self._user.get("is_live"):
            return ""
        return (self._user.get("title") or "").strip()

    def _paint_live_title(self, painter: QPainter) -> None:
        """开播时把直播标题钉在封面左上角：这些字原来藏在悬浮提示里，不悬停就看不到"""
        title = self.cover_caption()
        if not title:
            return
        font = QFont(self.font())
        font.setPixelSize(self.PILL_FONT_PIXEL)
        painter.setFont(font)
        metrics = painter.fontMetrics()   # 用画布的实际 DPI 量，别按默认屏幕估
        # 封面宽度是硬上限，不像列表列那样可以加宽列，超长只能省略号收尾
        limit = self.width() - 2 * self.PILL_MARGIN - 2 * self.PILL_PAD
        text = metrics.elidedText(title, Qt.TextElideMode.ElideRight, limit)
        pill = QRectF(self.PILL_MARGIN, self.PILL_MARGIN,
                      min(limit, metrics.horizontalAdvance(text)) + 2 * self.PILL_PAD,
                      self.PILL_HEIGHT)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(0, 0, 0, 150)))
        painter.drawRoundedRect(pill, 5, 5)
        painter.setPen(QPen(QColor(255, 255, 255, 240)))
        painter.drawText(pill.adjusted(self.PILL_PAD, 0, -self.PILL_PAD, 0),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)

    def _paint_ripple(self, painter: QPainter, bar: QRectF, center: QPointF, color: QColor) -> None:
        """红点呼出一圈虚影向外摊开，越走越淡，散干净了再起下一波

        用径向渐变糊成一团光，不是一条描边：最亮那一档走在中段，点边留一层薄光接着，
        所以既看得出圈在向外跑，又不会断成一枚独立的甜甜圈。
        浓度先撑住、走完 RIPPLE_FADE 才开始收，「扩大」看得清，收尾是真消失。
        外沿最多走到状态条上下沿，再远就被切平。
        """
        painter.save()
        painter.setClipRect(bar)
        painter.setPen(Qt.PenStyle.NoPen)
        progress = (time.monotonic() / self.RIPPLE_PERIOD) % 1.0
        dot_radius = self.DOT_ACTIVE / 2.0
        outer = dot_radius + progress * self.RIPPLE_SPAN
        if outer - dot_radius < 0.8:      # 刚起波时虚影全压在点边上，画了也看不见
            painter.restore()
            return
        peak = int(self.RIPPLE_PEAK * min(1.0, (1.0 - progress) / self.RIPPLE_FADE))
        # 最亮的那一档不在点边，而是走在虚影的前中段：这样圈是真的向外跑，
        # 但点边始终留一层薄光接着，不至于断成一枚独立的甜甜圈。
        crest = dot_radius + (outer - dot_radius) * 0.62
        gradient = QRadialGradient(center, outer, center)
        gradient.setColorAt(0.0, _with_alpha(color, int(peak * 0.35)))         # 点内那截会被红点盖住
        gradient.setColorAt(dot_radius / outer, _with_alpha(color, int(peak * 0.35)))
        gradient.setColorAt(crest / outer, _with_alpha(color, peak))
        gradient.setColorAt(1.0, _with_alpha(color, 0))                       # 外沿散到没有
        painter.setBrush(QBrush(gradient))
        painter.drawEllipse(center, outer, outer)
        painter.restore()

    def _sync_ripple(self) -> None:
        """只有「在录 + 看得见」才转动画：卡片切走、被筛掉时没必要空转重绘"""
        if self._record is not None and self.isVisible():
            if not self._ripple.isActive():
                self._ripple.start()
        else:
            self._ripple.stop()

    def _advance_ripple(self) -> None:
        # 祖先被隐藏时 Qt 不会给子控件发 hideEvent（切到列表视图就是这种），
        # 所以每一拍自己验一次可见性，发现看不见就顺手停掉
        if self._record is None or not self.isVisible():
            self._ripple.stop()
            return
        top = self.height() - self.BAR_HEIGHT - 2
        self.update(QRect(0, top, self.width(), self.BAR_HEIGHT + 4))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._sync_ripple()

    def hideEvent(self, event) -> None:
        self._ripple.stop()
        super().hideEvent(event)
