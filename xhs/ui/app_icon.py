"""应用图标：托盘与任务栏共用一枚，全部代码画出来

原来那枚是平台 logo 的复刻（红底白「书」）——自用无妨，一旦打包分发就是商标问题。
这枚换成「录制」自己的语义：深灰底 + 白环 + 红点。试稿比过三版，开口环在 16px 上
读起来像字母 C，两圈淡红扩散圈糊成噪点，只有整圈白环 + 红点这档环和点都分得开。

运行时不依赖任何图片资源；仓库根那枚 app_icon.ico 是给打包用的（exe 文件图标和
快捷方式只能从 .ico 取），由 tools/make_icon.py 调下面的 pixmap() 生成，画法改了要重跑。

配色不跟主题走：托盘住在系统外壳里，跟着浅色主题变浅会在浅色任务栏上失去轮廓；
底给深，再描一圈极淡的亮边兜住深色任务栏，两头都认得出形状。
"""

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QIcon, QLinearGradient, QPainter, QPainterPath,
                           QPen, QPixmap)

# 任务栏/托盘真正用得上的几档：100% 缩放是 16px，125% / 150% / 200% 各对应 20 / 24 / 32，
# 48 兜住更高缩放和 Alt-Tab 的大图。再大的尺寸既用不上，也只是白画几张位图。
SIZES = (16, 20, 24, 32, 48)
TILE_TOP = QColor(66, 66, 76)
TILE_BOTTOM = QColor(28, 28, 34)
TILE_EDGE = QColor(255, 255, 255, 42)     # 那圈亮边：深色任务栏上全靠它兜轮廓
RED = QColor(232, 32, 60)                 # 录制红：比界面两档 accent 折中，深浅底都够亮
RING = QColor(255, 255, 255, 238)


def pixmap(size: int) -> QPixmap:
    """按边长画一枚：所有笔画都按边长比例给，五档缩放后是同一个形状"""
    image = QPixmap(size, size)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    inset = max(1.0, size * 0.04)
    side = size - inset * 2
    path = QPainterPath()
    path.addRoundedRect(QRectF(inset, inset, side, side), side * 0.22, side * 0.22)
    gradient = QLinearGradient(0, inset, 0, inset + side)
    gradient.setColorAt(0.0, TILE_TOP)
    gradient.setColorAt(1.0, TILE_BOTTOM)
    painter.fillPath(path, QBrush(gradient))
    painter.setPen(QPen(TILE_EDGE, max(1.0, size * 0.025)))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(path)

    center = QPointF(size / 2.0, size / 2.0)
    painter.setPen(QPen(RING, side * 0.10))
    painter.drawEllipse(center, side * 0.27, side * 0.27)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(RED)
    painter.drawEllipse(center, side * 0.135, side * 0.135)
    painter.end()
    return image


def icon() -> QIcon:
    result = QIcon()
    for size in SIZES:
        result.addPixmap(pixmap(size))
    return result
