"""主题：配色表、样式表生成、颜色 / 字体 / 胶囊样式助手"""

import hashlib
import os
from xhs import config
from xhs import settings
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase
from PySide6.QtWidgets import QFrame, QLabel, QProxyStyle, QStyle, QWidget


# ============================================================
# 主题：明亮（默认）/ 深色，配色集中在 THEME_PALETTES，样式表按主题生成
# ============================================================
THEME_PALETTES = {
    config.THEME_LIGHT: {
        "text": (26, 26, 31),            # 主文字
        "overlay": (0, 0, 0),            # 半透明填充基色（浅底用黑压暗）
        "popup": (255, 255, 255),        # 菜单 / 下拉 / 气泡底色
        "mica_base": (243, 243, 245),    # Mica 表面基准色
        "mica_tint": (250, 250, 252),    # 取不到壁纸时的中性色调
        "accent": (230, 26, 56),         # 品牌红（浅底上加深保证对比度）
        "accent_hover": (242, 45, 74),
        "accent_pressed": (198, 16, 46),
        "accent_soft": (214, 30, 60),    # 标题栏品牌字色
        "ok": (22, 143, 74),             # 录制中 / 直播中
        "info": (32, 105, 216),          # 监控中：只是轮询开着，比「在录」低一档，用冷色
        "check": (22, 143, 74),          # 勾选框底色：绿底配白勾
        "warn": (183, 116, 0),
        "err": (200, 44, 44),            # 登录态失效 / ✕ 字色
        "link": (46, 107, 209),          # 平台标签文字
        "sunken_alpha": 0.05,            # 输入框 / 日志凹陷底
        "noise": (170, 230),             # 噪点灰度取值区间
        "shadow_alpha": 55,              # 卡片投影浓度
    },
    config.THEME_DARK: {
        "text": (245, 239, 247),
        "overlay": (255, 255, 255),
        "popup": (35, 35, 35),
        "mica_base": (32, 32, 32),
        "mica_tint": (43, 43, 51),
        "accent": (255, 36, 66),
        "accent_hover": (255, 60, 92),
        "accent_pressed": (217, 27, 54),
        "accent_soft": (255, 92, 122),
        "ok": (74, 222, 128),            # 录制中 / 直播中
        "info": (96, 168, 255),          # 监控中
        "check": (35, 170, 90),          # 勾选框底色：偏深的绿，白勾才看得清
        "warn": (255, 193, 77),
        "err": (255, 107, 107),          # 登录态失效（也是 ✕ 那两枚的字色）
        "link": (127, 182, 255),
        "sunken_alpha": 0.14,
        "noise": (96, 160),
        "shadow_alpha": 90,
    },
}

# 窗口控制与关闭的字形：全项目只认这四个，别再各写各的
# （「X」「×」「✕」三种关闭符号原先在标题栏、设置窗、确认框各用一个，同一件事看着像三个控件）
# 最大化/还原那版试过 ▣ ❐ ◱  ▩，都在 13px 上比过：要么糊成一团，要么看着像选中态
MINIMIZE_GLYPH = "−"    # U+2212 真减号，比 ASCII 连字符宽且垂直居中
CLOSE_GLYPH = "✕"       # U+2715 乘叉
MAXIMIZE_GLYPH = "□"    # U+25A1 空心方框
RESTORE_GLYPH = "⧉"     # U+29C9 两枚叠框，Windows 的「还原」就是这个形状

_CHECK_ICON_URL = None

def check_icon_url() -> str:
    """勾选面板的对勾图标（文件路径）

    一旦在样式表里自定义了 indicator 的背景，Qt 就不再画原生勾形，必须自己给 :checked 配图；
    而 QSS 的 url() 只认文件路径（不认 data URI），所以先把一枚白色对勾 SVG 落到配置目录，
    进程内只写一次，两套主题共用（勾永远是白底上的白勾，压在品牌红上）。写盘失败返回空串，
    勾选框退化为纯色块。
    """
    global _CHECK_ICON_URL
    if _CHECK_ICON_URL is None:
        url = ""
        try:
            os.makedirs(config.CONFIG_DIR, exist_ok=True)
            target = os.path.join(config.CONFIG_DIR, "field_check.svg")
            with open(target, "w", encoding="utf-8") as handle:
                handle.write('<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16">'
                             '<path d="M3 8.4 L6.3 11.7 L13 4.6" stroke="#FFFFFF" stroke-width="2.2" '
                             'fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>')
            url = target.replace("\\", "/")
        except Exception:
            url = ""
        _CHECK_ICON_URL = url
    return _CHECK_ICON_URL

def _build_qss(palette: dict) -> str:
    """按配色表生成整份样式表：两套主题共用同一套规则，避免各自维护走样"""

    def rgb(key: str) -> str:
        red, green, blue = palette[key]
        return f"rgb({red}, {green}, {blue})"

    def rgba(key: str, alpha: float) -> str:
        red, green, blue = palette[key]
        return f"rgba({red}, {green}, {blue}, {alpha})"

    fill = rgba("overlay", 0.045)
    fill_strong = rgba("overlay", 0.08)
    border = rgba("overlay", 0.07)
    accent_rgb, accent_soft_rgb = rgb("accent"), rgb("accent_soft")
    sunken = rgba("overlay", palette["sunken_alpha"])
    check_url = check_icon_url()      # 勾形图标：写盘失败时留空，勾选框退化为纯色块
    check_image = f'image: url("{check_url}");' if check_url else ""
    return f"""
QWidget {{ color: {rgb("text")}; background: transparent; }}

QToolTip {{
    background: {rgba("popup", 0.96)}; color: {rgb("text")};
    border: 1px solid {rgba("overlay", 0.10)}; border-radius: 6px; padding: 6px 8px;
}}

QFrame#glassCard {{
    background: {fill};
    border: 1px solid {border};
    border-radius: 8px;
}}
QFrame#statusPill {{
    background: {rgba("overlay", 0.05)};
    border: 1px solid {border};
    border-radius: 12px;
}}
QFrame#divider {{ background: {border}; border: none; }}
QLabel#appTitle {{ font-size: 15px; font-weight: 700; color: {rgba("text", 0.88)}; }}
QLabel#appSubTitle {{ font-size: 11px; color: {rgba("text", 0.45)}; }}
QLabel#sectionTitle {{
    font-size: 12px; font-weight: 700; color: {rgba("text", 0.80)}; letter-spacing: 1px;
}}
QLabel#fieldLabel {{ font-size: 12px; color: {rgba("text", 0.60)}; }}
QLabel#fieldValue {{ font-size: 12px; font-weight: 600; }}
QLabel#dialogTitle {{ font-size: 15px; font-weight: 700; color: {rgba("text", 0.92)}; }}
QLabel#dialogBody {{ font-size: 13px; color: {rgba("text", 0.80)}; }}

QTextEdit#logView {{
    background: {sunken};
    border: 1px solid {border};
    border-radius: 6px;
    font-family: "Consolas", "Courier New", monospace;
    font-size: 11px;
    color: {rgba("text", 0.72)};
    padding: 4px 6px;
}}
QTextEdit#logView::selection {{ background: {accent_rgb}; color: #FFFFFF; }}

QLineEdit, QSpinBox {{
    background: {sunken};
    border: 1px solid {rgba("overlay", 0.08)};
    border-radius: 6px; padding: 6px 10px; font-size: 12px;
    selection-background-color: {accent_rgb};
}}
QLineEdit:focus, QSpinBox:focus {{
    border: 1px solid {rgba("accent", 0.85)}; background: {rgba("overlay", 0.06)};
}}
QSpinBox::up-button, QSpinBox::down-button {{
    width: 0px; height: 0px; border: none; background: transparent;
}}

QPushButton#primaryBtn {{
    background: {accent_rgb}; border: none; border-radius: 6px;
    color: #FFFFFF; font-size: 13px; font-weight: 600;
}}
QPushButton#primaryBtn:hover {{ background: {rgb("accent_hover")}; }}
QPushButton#primaryBtn:pressed {{ background: {rgb("accent_pressed")}; }}
QPushButton#primaryBtn:disabled {{
    background: {rgba("overlay", 0.10)}; color: {rgba("text", 0.40)};
}}

QPushButton#stopBtn {{
    background: {rgba("overlay", 0.05)}; border: 1px solid {rgba("overlay", 0.09)};
    border-radius: 6px; font-size: 13px; font-weight: 600;
}}
QPushButton#stopBtn:hover {{
    background: {rgba("overlay", 0.09)}; border-color: {rgba("overlay", 0.13)};
}}

QPushButton#ghostBtn {{
    background: {rgba("overlay", 0.04)}; border: 1px solid {rgba("overlay", 0.08)};
    border-radius: 6px; font-size: 12px; padding: 6px 14px;
}}
QPushButton#ghostBtn:hover {{
    background: {fill_strong}; border-color: {rgba("overlay", 0.12)};
}}

QPushButton#iconBtn, QPushButton#iconClose {{
    background: {rgba("overlay", 0.05)}; border: 1px solid {border};
    border-radius: 6px; font-size: 13px; font-weight: 600; color: {rgba("text", 0.80)};
}}
QPushButton#iconBtn:hover {{
    background: {fill_strong}; border-color: {rgba("overlay", 0.22)}; color: {rgb("text")};
}}
QPushButton#iconClose {{ color: {rgba("err", 0.95)}; }}
QPushButton#iconClose:hover {{
    background: #C42B1C; border-color: #C42B1C; color: #FFFFFF;
}}

/* 标题栏的窗口控制：单开两个名字，让它们跟 #iconBtn 那些真动作按钮区分开。
   三枚共用一枚底框（读起来是一个控件），框内各自留 2px 空，悬停的圆角填充才不会
   顶到外框的圆角上。 */
QFrame#captionGroup {{
    background: {rgba("overlay", 0.05)}; border: 1px solid {border}; border-radius: 8px;
}}
QPushButton#captionBtn, QPushButton#captionClose {{
    background: transparent; border: none; border-radius: 6px;
    font-size: 13px; font-weight: 600; color: {rgba("text", 0.72)};
}}
QPushButton#captionBtn:hover {{
    background: {rgba("overlay", 0.10)}; color: {rgb("text")};
}}
QPushButton#captionClose {{ color: {rgba("err", 0.95)}; }}
QPushButton#captionClose:hover {{
    background: #C42B1C; color: #FFFFFF;
}}

/* 卡片/列表行的「···」：参考图里它是三个直接落在卡片上的点，没有盒子。
   标题栏和设置框里的按钮仍走 #iconBtn 那圈描边，所以单开一个名字。 */
QPushButton#moreBtn {{
    background: transparent; border: none;
    font-size: 13px; font-weight: 600; color: {rgba("text", 0.80)};
}}
QPushButton#moreBtn:hover {{
    background: {rgba("overlay", 0.08)}; border-radius: 6px; color: {rgb("text")};
}}

QListWidget {{ background: transparent; border: none; }}

/* ---------- 关注列表：卡片视图 ---------- */
QFrame#followCard {{
    background: {rgba("overlay", 0.05)};
    border: 1px solid {border};
    border-radius: 8px;
}}
QFrame#followCard:hover {{
    background: {fill_strong};
    border-color: {rgba("overlay", 0.14)};
}}
QLabel#cardNick {{ font-size: 12px; font-weight: 600; }}
QLabel#cardFan {{ font-size: 12px; color: {rgba("text", 0.46)}; }}

/* ---------- 关注列表：列表视图 ---------- */
QFrame#followRow {{
    background: {rgba("overlay", 0.03)};
    border: 1px solid {rgba("overlay", 0.06)};
    border-radius: 6px;
}}
QFrame#followRow:hover {{ background: {rgba("overlay", 0.07)}; }}
QFrame#listHeader {{
    background: {rgba("overlay", 0.04)};
    border: 1px solid {rgba("overlay", 0.06)};
    border-radius: 6px;
}}
QLabel#listHead {{ font-size: 12px; color: {rgba("text", 0.45)}; }}
QLabel#listCell {{ font-size: 12px; color: {rgba("text", 0.78)}; }}
/* 房间号、最近录制时间两列共用一套字号与颜色，改动时必须同步 */
QLabel#listRoom, QLabel#listTime {{ font-size: 12px; color: {rgba("text", 0.88)}; }}
QLabel#emptyHint {{ font-size: 12px; color: {rgba("text", 0.45)}; }}

/* ---------- 工具栏 ---------- */
QComboBox {{
    background: {rgba("overlay", 0.04)}; border: 1px solid {rgba("overlay", 0.08)};
    border-radius: 6px; padding: 3px 6px; font-size: 12px;
    color: {rgba("text", 0.86)};
}}
QComboBox:hover {{ background: {rgba("overlay", 0.07)}; }}
QComboBox::drop-down {{ width: 18px; border: none; background: transparent; }}
/* 空值时框里只剩字段名提示：压淡一档，免得和「已经选上了」长得一样 */
QComboBox[placeholderShown="true"] {{ color: {rgba("text", 0.42)}; }}
/* 下箭头与弹出层均由 ArrowComboBox 自绘：样式表里用 border 拼三角会被 Qt 画成小黑块，
   而弹出层容器是私有类，::item / ID 选择器在各平台表现不一致 */

QPushButton#viewToggle {{
    background: {rgba("overlay", 0.04)}; border: 1px solid {rgba("overlay", 0.08)};
    border-radius: 6px; font-size: 12px; padding: 4px 12px;
    color: {rgba("text", 0.66)};
}}
QPushButton#viewToggle:hover {{ background: {fill_strong}; }}
QPushButton#viewToggle:checked {{
    background: {rgba("accent", 0.16)}; border-color: {rgba("accent", 0.55)};
    color: {accent_soft_rgb};
}}
QPushButton#pageBtn {{
    background: {rgba("overlay", 0.04)}; border: 1px solid {rgba("overlay", 0.08)};
    border-radius: 6px; font-size: 12px; padding: 3px 10px;
}}
QPushButton#pageBtn:hover {{ background: {rgba("overlay", 0.09)}; }}
QPushButton#pageBtn:checked {{
    background: {rgba("overlay", 0.12)}; border: 1px solid {rgba("overlay", 0.18)};
}}
QPushButton#pageBtn:disabled {{ color: {rgba("text", 0.30)}; background: {rgba("overlay", 0.02)}; }}

QPushButton#tagChip {{
    background: {rgba("overlay", 0.05)}; border: 1px solid {rgba("overlay", 0.10)};
    border-radius: 4px; font-size: 11px; padding: 2px 8px;
    color: {rgba("text", 0.70)};
}}
QPushButton#tagChip:hover {{
    background: {fill_strong}; border-color: {rgba("accent", 0.50)};
    color: {accent_soft_rgb};
}}
QPushButton#tagChip:pressed {{ background: {rgba("accent", 0.16)}; }}

QMenu {{
    background: {rgb("popup")}; border: 1px solid {rgba("overlay", 0.10)};
    border-radius: 8px; padding: 6px;
}}
/* 菜单项跟下拉弹层走同一套：悬停只压一层灰底，不铺品牌红。
   原来是红底白字，一整列动作项点哪个都像在按删除键。行高给足，别挤成一坨。 */
QMenu::item {{
    padding: 9px 26px 9px 14px; border-radius: 6px; font-size: 13px;
    color: {rgba("text", 0.86)};
}}
QMenu::item:selected {{ background: {fill_strong}; color: {rgb("text")}; }}
QMenu::item:disabled {{ color: {rgba("text", 0.30)}; }}
QMenu::separator {{ height: 1px; background: {rgba("overlay", 0.10)}; margin: 6px 8px; }}

/* 「显示字段」多选勾选面板：放在 QMenu 里但由复选框接管交互 */
QMenu QFrame#fieldsPanel {{ background: transparent; }}
QMenu QCheckBox {{
    color: {rgba("text", 0.82)}; font-size: 12px; spacing: 8px; padding: 3px 8px;
}}
QMenu QCheckBox:hover {{ color: {rgb("text")}; }}
QMenu QCheckBox::indicator {{
    width: 15px; height: 15px; border-radius: 4px;
    border: 1px solid {rgba("overlay", 0.28)}; background: {rgba("overlay", 0.04)};
}}
QMenu QCheckBox::indicator:hover {{ border-color: {rgba("check", 0.8)}; }}
QMenu QCheckBox::indicator:checked {{
    background: {rgb("check")}; border-color: {rgb("check")};
    {check_image}
}}

QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0px; }}
QScrollBar::handle:vertical {{
    background: {rgba("overlay", 0.14)}; border-radius: 4px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {rgba("overlay", 0.24)}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
QScrollBar:horizontal {{ height: 0px; }}
"""

_QSS_CACHE = {}

def theme_qss() -> str:
    """当前主题的样式表（按主题缓存，切换主题时不会重复生成）"""
    theme_name = settings.current_theme()
    if theme_name not in _QSS_CACHE:
        _QSS_CACHE[theme_name] = _build_qss(THEME_PALETTES[theme_name])
    return _QSS_CACHE[theme_name]

def _palette() -> dict:
    return THEME_PALETTES[settings.current_theme()]

def _rgb(key: str) -> str:
    red, green, blue = _palette()[key]
    return f"rgb({red}, {green}, {blue})"

def _rgba(key: str, alpha: float) -> str:
    red, green, blue = _palette()[key]
    return f"rgba({red}, {green}, {blue}, {alpha})"

def _qcolor(key: str, alpha: int = 255) -> QColor:
    """按 palette 取 QColor，供自绘部件（封面描边、箭头等）使用"""
    color = QColor(*_palette()[key])
    color.setAlpha(alpha)
    return color


def default_font() -> QFont:
    """挑选可用的中文字体，避免出现方框乱码"""
    families = set(QFontDatabase.families())
    for name in ("Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Noto Sans SC"):
        if name in families:
            return QFont(name, 10)
    return QFont()


class _KeyboardFocusStyle(QProxyStyle):
    """焦点框只在键盘聚焦时画：Windows 基础样式鼠标点完也留一圈虚线框"""

    def drawPrimitive(self, element, option, painter, widget=None) -> None:
        if (element == QStyle.PrimitiveElement.PE_FrameFocusRect
                and not (option.state & QStyle.StateFlag.State_KeyboardFocusChange)):
            return
        super().drawPrimitive(element, option, painter, widget)


def install_app_style(app) -> None:
    """套全局代理样式：藏掉鼠标点击产生的焦点框，Tab 键盘聚焦仍保留"""
    app.setStyle(_KeyboardFocusStyle(app.style().objectName()))


class _RoundedTipFilter(QObject):
    """把全局提示窗改成逐像素透明，样式表里 QToolTip 的 border-radius 这才算数

    提示窗（QTipLabel）每次弹出都是新建、收起即销毁，没有「开局改一次管后面所有次」
    这条路，只能挂在事件上现抓现改。时机要赶在 Qt 建原生窗口（PlatformSurface 事件）
    之前——它收到的头一个事件就是构造函数 setPalette 触发的 PaletteChange，正好赶上。
    改晚了窗口已经按不透明建好，圆角外那四个小方块就留着系统底色当「角」。
    """

    def eventFilter(self, obj, event) -> bool:
        if (event.type() != QEvent.Type.PaletteChange
                or obj.metaObject().className() != "QTipLabel"):
            return False
        if obj.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground):
            return False        # 一次弹出会连发好几回，改到位就别再动它
        obj.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        # 同下拉弹层：这一档只能用复数形式的 setWindowFlags，且系统投影在透明窗上是硬黑边
        obj.setWindowFlags(obj.windowFlags() | Qt.WindowType.NoDropShadowWindowHint)
        obj.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        return False


_TIP_FILTER = None   # installEventFilter 不接管所有权：不留引用当场就被回收，过滤器静默失效


def install_rounded_tooltips(app) -> None:
    """让悬浮提示真带圆角（Windows 上提示窗默认按不透明建，圆角处会露出四个方角）"""
    global _TIP_FILTER
    _TIP_FILTER = _RoundedTipFilter()
    app.installEventFilter(_TIP_FILTER)


def disable_combo_roll(app) -> None:
    """关掉下拉弹层的展开动画，它是「卡片下面一条黑影」的来源

    Qt 给 QComboBox 的容器装的是 QRollEffect：一格格把窗口高度撑开来「展」出弹层。撑开
    的那几条是窗口里还没画过的像素，Windows 给的是纯黑。弹层是逐像素透明的（圆角卡片外
    那圈空位本该透出底下的内容），而这条动画压根不认逐像素 alpha —— 实测展开过程中弹层
    最底一行恒为 #000000，动画一结束又恢复正常，正是那条跟着扫下来的黑影。
    菜单和提示逐帧抓过，展开过程中都没有这种黑像素，所以只按下拉这一档。
    """
    app.setEffectEnabled(Qt.UIEffect.UI_AnimateCombo, False)


def _divider() -> QFrame:
    """分隔线：颜色写在样式表 QFrame#divider 里，切换主题时自动跟着变"""
    line = QFrame()
    line.setObjectName("divider")
    line.setFixedHeight(1)
    return line


def _apply_style(widget: QWidget, style: str) -> None:
    """幂等地套样式表：内容没变就不碰

    setStyleSheet 每次都要让 Qt 重新解析一遍样式串并重 polish 整个子树，
    一行里有 3 个胶囊标签，几百行高频重建就是上千次解析。
    """
    if widget.property("appliedStyle") != style:
        widget.setStyleSheet(style)
        widget.setProperty("appliedStyle", style)

_PILL_STYLE_CACHE = {}   # (color, background) -> 样式串：状态色取值有限，串本身可长期复用

def _pill_style(label: QLabel, text: str, color: str, background: str,
                min_width: int = 0) -> None:
    """把标签渲染成小圆角胶囊（平台 / 直播状态 / 录制状态）

    必须用 #objectName 选择器限定：裸声明会连带作用到该控件弹出的 QToolTip
    （提示框是它的子级 QLabel），录制中的红胶囊曾把悬浮提示刷成红底红字。
    """
    name = label.objectName()
    if not name:
        label.setObjectName("pill")
        name = "pill"
    key = (name, color, background)
    style = _PILL_STYLE_CACHE.get(key)
    if style is None:
        style = (f"QLabel#{name}{{background:{background};border:1px solid transparent;"
                 f"border-radius:7px;color:{color};font-size:12px;font-weight:600;"
                 f"padding:2px 10px;}}")
        _PILL_STYLE_CACHE[key] = style
    _apply_style(label, style)
    if label.text() != text:
        label.setText(text)
    if label.minimumWidth() != min_width:
        label.setMinimumWidth(min_width)
    if not label.property("pillCentered"):
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setProperty("pillCentered", True)


def _format_count(value: int) -> str:
    """数量格式化：12345 -> 1.2万"""
    if value >= 100000000:
        return f"{value / 100000000:.1f}亿"
    if value >= 10000:
        return f"{value / 10000:.1f}万"
    return str(value)

def _seed_colors(seed: str):
    """由字符串稳定推导一对渐变色，用于没有图片时的占位"""
    digest = hashlib.md5((seed or "xhs").encode("utf-8", "ignore")).hexdigest()
    hue = int(digest[:4], 16) % 360
    return QColor.fromHsl(hue, 96, 56), QColor.fromHsl((hue + 42) % 360, 88, 36)
