"""窗体外壳：Mica 背景与自绘标题栏"""

import random
import sys
from xhs import config
from xhs import settings
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget
from xhs.ui import theme


MICA_TINT_RATIO = 0.08            # Mica 只吸收约 8% 的壁纸色调
MICA_CORNER_RADIUS = 8            # Fluent 窗口圆角
MICA_NOISE_CACHE = {}             # 主题 -> 噪点纹理（两套主题各自缓存一张）

def _desktop_tint() -> QColor:
    """取桌面壁纸主色，用于模拟 Mica "随壁纸着色" 的特性；取不到时返回中性色"""
    fallback = QColor(*theme._palette()["mica_tint"])
    if sys.platform != "win32":
        return fallback
    try:
        import ctypes
        buffer = ctypes.create_unicode_buffer(260)
        if not ctypes.windll.user32.SystemParametersInfoW(0x0073, 260, buffer, 0):
            return fallback
        image = QImage(buffer.value)
        if image.isNull():
            return fallback
        thumbnail = image.scaled(1, 1, Qt.AspectRatioMode.IgnoreAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
        color = QColor(thumbnail.pixel(0, 0))
        return color if color.isValid() else fallback
    except Exception:
        return fallback

def _mica_surface() -> QColor:
    """Mica 表面色：主题基准色 + 微量壁纸色调，并压制饱和度与亮度，保持"单调"质感"""
    theme_name = settings.current_theme()
    base = QColor(*theme._palette()["mica_base"])
    tint = _desktop_tint()
    surface = QColor(
        int(base.red() + (tint.red() - base.red()) * MICA_TINT_RATIO),
        int(base.green() + (tint.green() - base.green()) * MICA_TINT_RATIO),
        int(base.blue() + (tint.blue() - base.blue()) * MICA_TINT_RATIO),
    )
    hue, saturation, lightness, alpha = surface.getHsl()
    # 深色主题压在暗部，明亮主题抬到接近白色的高亮区
    bounds = (30, 40) if theme_name == config.THEME_DARK else (232, 244)
    surface.setHsl(hue, min(saturation, 28), min(max(lightness, bounds[0]), bounds[1]), alpha)
    return surface

def _mica_noise() -> QPixmap:
    """一次性生成的细噪点纹理，是 Mica 材质的标志性质感（极低对比度）"""
    theme_name = settings.current_theme()
    cached = MICA_NOISE_CACHE.get(theme_name)
    if cached is not None:
        return cached
    size = 128
    low, high = theme._palette()["noise"]
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    for y in range(size):
        for x in range(size):
            value = random.randint(low, high)
            image.setPixelColor(x, y, QColor(value, value, value))
    noise = QPixmap.fromImage(image)
    MICA_NOISE_CACHE[theme_name] = noise
    return noise


class Backdrop(QWidget):
    """Mica 底纹：随壁纸着色的不透明表面 + 极细噪点 + 发丝描边；同时承载边缘拖拽缩放"""

    EDGE_MARGIN = 8
    CORNER_RADIUS = MICA_CORNER_RADIUS
    BORDER_ALPHA = 26      # 默认描边浓度（发丝级）；浮在其他窗口之上时可加深

    def __init__(self, parent=None, border_alpha: int = BORDER_ALPHA):
        super().__init__(parent)
        self.setMouseTracking(True)
        self._surface = _mica_surface()
        self._border_alpha = border_alpha

    def refresh_theme(self) -> None:
        """切换主题后重新取表面色并重绘"""
        self._surface = _mica_surface()
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5),
                            self.CORNER_RADIUS, self.CORNER_RADIUS)
        painter.setClipPath(path)

        # 表面压平，仅保留极轻微的上下明度差（Mica 的"单调"渐变）
        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0.0, self._surface.lighter(104))
        gradient.setColorAt(1.0, self._surface.darker(108))
        painter.fillPath(path, QBrush(gradient))

        # 细噪点：Mica 材质的标志性质感
        painter.setOpacity(0.05)
        painter.drawTiledPixmap(self.rect(), _mica_noise())
        painter.setOpacity(1.0)

        painter.setClipping(False)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(theme._qcolor("overlay", self._border_alpha), 1.0))
        painter.drawPath(path)

    def _edges(self, pos):
        x, y = pos.x(), pos.y()
        width, height = self.width(), self.height()
        margin = self.EDGE_MARGIN
        edges = Qt.Edges(0)
        if x <= margin:
            edges = edges | Qt.Edge.LeftEdge
        if x >= width - margin:
            edges = edges | Qt.Edge.RightEdge
        if y <= margin:
            edges = edges | Qt.Edge.TopEdge
        if y >= height - margin:
            edges = edges | Qt.Edge.BottomEdge
        return edges

    def mousePressEvent(self, event) -> None:
        edges = self._edges(event.position().toPoint())
        handle = self.window().windowHandle()
        if edges and handle is not None and event.button() == Qt.MouseButton.LeftButton:
            handle.startSystemResize(edges)
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        edges = self._edges(event.position().toPoint())
        cursor = Qt.CursorShape.ArrowCursor
        left = bool(edges & Qt.Edge.LeftEdge)
        right = bool(edges & Qt.Edge.RightEdge)
        top = bool(edges & Qt.Edge.TopEdge)
        bottom = bool(edges & Qt.Edge.BottomEdge)
        if (left or right) and not (top or bottom):
            cursor = Qt.CursorShape.SizeHorCursor
        elif (top or bottom) and not (left or right):
            cursor = Qt.CursorShape.SizeVerCursor
        elif (left and top) or (right and bottom):
            cursor = Qt.CursorShape.SizeFDiagCursor
        elif (right and top) or (left and bottom):
            cursor = Qt.CursorShape.SizeBDiagCursor
        self.setCursor(cursor)
        super().mouseMoveEvent(event)

class TitleBar(QFrame):
    """自定义标题栏：拖动窗口、状态胶囊、设置、最小化 / 最大化 / 关闭"""

    settings_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("titleBar")
        self.setFixedHeight(48)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 9, 16, 9)
        layout.setSpacing(10)

        self._status = (False, False, 0)   # (监控中, 停止中, 在录路数)：切主题时按此重绘胶囊
        self._auth_expired = False         # 登录态是否已失效：与运行状态正交，单独一档
        self.brand = QLabel("小红书")
        self._paint_brand()
        title = QLabel("直播录制器")
        title.setObjectName("appTitle")
        version = QLabel(f"v{config.APP_VERSION}")
        version.setObjectName("appSubTitle")

        self.status_dot = QLabel()
        self.status_dot.setFixedSize(10, 10)
        self.status_label = QLabel("未启动")
        self.status_label.setObjectName("fieldLabel")

        pill = QFrame()
        pill.setObjectName("statusPill")
        pill.setFixedHeight(30)   # 与右侧「设置」按钮同高
        pill_layout = QHBoxLayout(pill)
        pill_layout.setContentsMargins(12, 6, 12, 6)
        pill_layout.setSpacing(8)
        pill_layout.addWidget(self.status_dot)
        pill_layout.addWidget(self.status_label)

        self.btn_settings = QPushButton("设置")
        self.btn_settings.setObjectName("ghostBtn")
        self.btn_settings.setFixedSize(60, 30)
        self.btn_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_settings.setToolTip("打开设置（监控开关 / 轮询间隔 / 输出目录）")
        self.btn_settings.clicked.connect(self.settings_clicked.emit)

        self.btn_min = QPushButton(theme.MINIMIZE_GLYPH)
        self.btn_max = QPushButton(theme.MAXIMIZE_GLYPH)
        self.btn_close = QPushButton(theme.CLOSE_GLYPH)
        self.btn_min.setObjectName("captionBtn")
        self.btn_max.setObjectName("captionBtn")
        self.btn_close.setObjectName("captionClose")
        for button, tip in ((self.btn_min, "最小化到任务栏"),
                            (self.btn_max, "最大化"),
                            (self.btn_close, "关闭到托盘（后台继续录制）")):
            button.setFixedSize(32, 24)
            button.setToolTip(tip)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_min.clicked.connect(lambda: self.window().showMinimized())
        self.btn_max.clicked.connect(self.toggle_maximized)
        self.btn_close.clicked.connect(lambda: self.window().close())

        # 三枚窗口控制装进同一枚底框：散着放太像「什么都没有」，各配一个盒子又会
        # 跟「设置」抢层级。合成一组后它们读起来是一个控件，而不是三个功能按钮。
        # 竖向尺寸是凑出来的：24（按钮）+ 2×2（内边距）+ 2×1（边框）= 30，正好等于
        # 胶囊和「设置」的高度。按钮再高 1px 就会顶到框的上沿、下面空一截。
        captions = QFrame()
        captions.setObjectName("captionGroup")
        captions_layout = QHBoxLayout(captions)
        captions_layout.setContentsMargins(2, 2, 2, 2)
        captions_layout.setSpacing(0)
        for button in (self.btn_min, self.btn_max, self.btn_close):
            captions_layout.addWidget(button)

        layout.addWidget(self.brand)
        layout.addWidget(title)
        layout.addWidget(version)
        layout.addStretch()
        layout.addWidget(pill)
        layout.addWidget(self.btn_settings)
        layout.addWidget(captions)
        self._paint_status()   # 小圆点没有默认底色，不涂一次就是「未启动」三个字前面空着

    def _paint_brand(self) -> None:
        # 品牌名走最克制的处理：纯文字品牌红，无底无边，只靠字重与字间距立住。
        # 比「直播录制器」小一档：两行字原来只差 1px，看着像同字号的两种颜色
        self.brand.setStyleSheet(
            f"background: transparent; border: none; color: {theme._rgb('accent_soft')};"
            " font-size: 12px; font-weight: 800; letter-spacing: 2px; padding: 0;")
        self.brand.setFixedHeight(30)   # 与右侧「设置」按钮同高

    def refresh_theme(self) -> None:
        """切换主题后重绘品牌字与状态胶囊"""
        self._paint_brand()
        self._paint_status()

    def set_status(self, running: bool, stopping: bool = False, recording: int = 0) -> None:
        # 进度每 400ms 合并刷一次，顺带把路数带回来：状态没变就返回，别重设样式表
        state = (running, stopping, recording)
        if state == self._status:
            return
        self._status = state
        self._paint_status()

    def set_auth_expired(self, expired: bool) -> None:
        """登录态失效是「一直成立到被修好」的状态，跟四档运行态正交，单独存一个标记"""
        if self._auth_expired == expired:
            return
        self._auth_expired = expired
        self._paint_status()

    def _paint_status(self) -> None:
        """按 _status / _auth_expired / config.FFMPEG_PATH 重绘：
        停止中 > 缺少 ffmpeg > 登录态失效 > 录制中 > 监控中 > 未启动

        三档颜色按「出事没有 → 在不在出活 → 只是开着」排：失效红、录制绿、监控蓝。
        蓝色是「轮询开着」这种既不危险也不产出的中间态；录制中盖过监控中，是因为这行字
        在窗口被别的窗盖住时唯一还在说话，「在录几路」比「轮询开着」值得先看。

        缺 ffmpeg 压在登录态失效前面：两者都是红，但失效修得好（设置里重新粘贴 sid 就活），
        缺工具则整个程序录不出任何东西，重新粘贴 sid 也不会让它开始出活，先说没救的那一件。
        它没有 setter：FFMPEG_PATH 在 import 时就定死了，进程内不会变，唯一的解法是换文件
        再重启 —— 所以这里直接读 config，省掉一个永远只会被调用一次的开关。

        登录态失效排在录制中前面，但不把路数丢掉：sid 死了只是发现不了新开播，
        已经在录的流照旧在跑。只写「登录态失效」会让人以为程序停摆了而直接退出，
        反而把在录的几路一起掐掉。
        """
        running, stopping, recording = self._status
        if stopping:
            text, color, tip = "停止中", theme._rgb("warn"), ""
        elif config.FFMPEG_PATH is None:
            text, color = "缺少 ffmpeg", theme._rgb("err")
            tip = ("没找到 ffmpeg.exe，录制功能整条不可用（关注列表、设置这些照常）。\n"
                   "把它加入系统 PATH，或直接放到本程序同目录，然后重启程序。")
        elif self._auth_expired:
            text = f"登录态失效 · 在录 {recording} 路" if recording else "登录态失效"
            color = theme._rgb("err")
            tip = ("登录态（sid）已过期，拉不到关注列表了：打开「设置 → 登录态」重新粘贴。\n"
                   "不影响正在录制的几路，但新开播的主播不会再自动录。")
        elif recording:
            text, color, tip = f"录制中 · {recording} 路", theme._rgb("ok"), ""
        elif running:
            text, color, tip = "监控中", theme._rgb("info"), ""
        else:
            text, color, tip = "未启动", theme._rgba("text", 0.55), ""
        self.status_dot.setStyleSheet(f"background:{color};border-radius:5px;")
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color:{color};font-size:12px;font-weight:600;")
        pill = self.status_label.parentWidget()
        if pill is not None:
            pill.setToolTip(tip)   # 空串等于清掉提示：只有需要指路的那一档才挂 tooltip

    def set_maximized(self, maximized: bool) -> None:
        """按窗口状态换字形和提示

        不走这枚按钮也能进/出最大化（双击标题栏、Win+↑、从托盘还原），所以字形
        由窗体的状态变化事件回灌，而不是点按钮时自己改。
        """
        self.btn_max.setText(theme.RESTORE_GLYPH if maximized else theme.MAXIMIZE_GLYPH)
        self.btn_max.setToolTip("向下还原" if maximized else "最大化")

    def toggle_maximized(self) -> None:
        window = self.window()
        if window.isMaximized():
            window.showNormal()
        else:
            window.showMaximized()

    def mousePressEvent(self, event) -> None:
        handle = self.window().windowHandle()
        if handle is not None and event.button() == Qt.MouseButton.LeftButton:
            handle.startSystemMove()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        self.toggle_maximized()
