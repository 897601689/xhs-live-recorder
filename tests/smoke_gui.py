"""界面层冒烟测试（离屏 Qt，不联网、不写用户配置）

覆盖这几轮改动里最容易出错的部分：
- 列 / 合计标签、就地刷新与主题刷新（不再整页重建）
- 进度事件合并、user_id 直接取自负载
- 日志抽屉：折叠缓冲、展开补齐、错误提醒计数
- 卡片上的「24」标签：只读、开了自动录制才显示；改状态只走「···」菜单（→ 主窗口回写）
- 筛选下拉的悬浮 × 清除、监听筛选、表头排序箭头与方向切换
- 下拉弹层的行高与行数（卡片/列表、筛选都一样，一行都不能少，末行得整条落在视口里；卡片描边到首末行还要留 3px，不然两行贴着圆角）
- 弹层宽度与按钮一致（圆角卡片画在视图身上，容器只透明垫底，且透明到上屏不带黑影）
- 每页数量：显示「N条/页」、真实值走 itemData，「全部」= 不分页
- 平台筛选：默认「全部平台」不筛，选具体平台时不误伤全量
- 卡片「···」在右下角、无边框纯点、右键开同一个菜单，菜单按「录制 / 查看 / 自动录制」三组分项
- 菜单里的「实时预览」：能点才可点，不能点要把原因写在悬浮提示上（缺 ffplay / 未开播 / 没有流地址），录制中提示多占一路拉流，窗口开着则换成「停止预览」；点了要真接到主窗口的 handler
- 「···」菜单是气泡菜单：尖角指着按钮，贴屏幕右边缘自动翻边，圆角外真空透明（不透明建窗会上屏成黑影）
- 封面录制圆点的扩散圈：定时器随「在录 + 可见」起停；卡片整层不再有悬浮提示
- 封面左上角那行字：只认直播标题，主播没填就留空（简介兜底试过一天就改回，别再用简介顶）
- 列表行的「录制中」胶囊与标题栏同一套色轴：绿 =「我在产出」、红只留给「出事」，品牌红不兼任状态色（卡片封面那枚红点是录制指示灯的拟物，故意不跟着改）
- 底部「录制中 N」：录制起止当场刷新，不靠整页重建（以前会停在旧数上，跟标题栏的「录制中 · 2 路」当面对不上）
- 悬浮提示窗：每次弹出都是新建的，得赶在建原生窗口前改成透明底，否则圆角处露四个方角
- 下拉弹层不带展开动画：Qt 的展开动画靠撑窗口高度来画，没画过的像素上屏是黑的
- 退出确认框：逐像素透明的圆角卡片（投影手画），模态、回车走主操作、三个出口都能关掉
- 普通弹出菜单（托盘、「显示字段」面板）：跟气泡菜单同一套圆角卡片，不再是实心方角
- 应用图标：托盘与任务栏共用一枚（深灰底 + 白环 + 红点），五档尺寸，16px 上环和点还分得开
- 标题栏最大化按钮：□/⧉ 两态由窗体的状态变化事件回灌，双击和 Win+↑ 这些不走按钮的入口也跟着换
- 标题栏状态胶囊：停止中 > 缺少 ffmpeg > 登录态失效 · 在录 N 路 > 录制中 · N 路 > 监控中 > 未启动；三色分开——缺工具与失效都走红（红完要在悬浮提示里指路）、录制绿、监控蓝（只是轮询开着，不产出）；窗口控制三枚扁平、共用一枚底框（24 + 2×2 内边距 + 2×1 边框 = 30，跟胶囊同高，填充上下各空 3px），与 #iconBtn 那类真动作按钮分开；条子高 48，上下留白刚好容下 30 高的胶囊；关闭符号标题栏/设置窗/确认框三处共用 theme.CLOSE_GLYPH（原来 ✕、X、× 各写各的）
- 托盘/退出流程：确认、停止请求、封箱等待
"""
import os
import pathlib
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import main as m                                                 # noqa: F401  入口能 import 通就算过一道
from xhs import config, preview, profiles, recorder, settings, state, utils
from xhs.ui import app_icon, following, main_window, preview_window, theme, widgets

try:
    import PySide6                                               # noqa: F401
except Exception:
    sys.exit("这个解释器没装 PySide6（main.py 会退回终端模式）。"
             "请用平时跑界面的那个环境执行，或先 pip install PySide6")

# 不要真的联网、也不要动用户目录里的配置与档案
settings.save_ui_settings = lambda: None
settings.set_view_mode = lambda mode: mode
# 日志同理：跑一遍测试不该在用户真实的 recorder.log 里留下满屏「程序启动过」的假线索
config.LOG_FILE = os.path.join(tempfile.mkdtemp(prefix="xhs_gui_log_"), "recorder.log")
profiles.mark_last_record = lambda user_id, nickname="", text=None: (text or "2026-09-30 22:00:00")
main_window.MainWindow._load_following = lambda self, verbose=False: None
main_window.MainWindow._auto_start_monitor = lambda self: None

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt                  # noqa: E402
from PySide6.QtGui import QContextMenuEvent, QCursor, QKeyEvent, QMouseEvent   # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QPushButton, QToolTip, QWidget      # noqa: E402

app = QApplication.instance() or QApplication([])

window = main_window.MainWindow()
window.resize(1200, 800)
window.show()
app.processEvents()

KEY = "u1_100"
USERS = [
    {"user_id": "u1", "nickname": "主播甲", "room_id": "100", "is_live": True,
     "avatar": "", "auto_record": True, "video_url": "http://x/flv", "last_record": ""},
    {"user_id": "u2", "nickname": "主播乙", "room_id": "", "is_live": False,
     "avatar": "", "auto_record": False, "video_url": "", "last_record": ""},
]

view = window.following_view
view.set_mode("card")
view.set_users([dict(u) for u in USERS], reset_page=True)
app.processEvents()
assert len(view._widgets) == 2, view._widgets
assert view.total_label.text() == "" or "磁盘" in view.total_label.text()

# 1) 就地刷新：同一批人只是状态微调时，控件实例必须保持不变
before = dict(view._widgets)
live = [dict(u) for u in USERS]
live[0]["is_live"] = False
view.set_users(live, reset_page=False)
app.processEvents()
assert view._widgets == before, "同批数据应走就地刷新，控件不该被替换"
print("1 就地刷新: 控件实例复用")

# 2) 换主题不再重建整页
original_theme = settings.current_theme()
settings.set_theme("light" if original_theme != "light" else "dark")
window._apply_theme()
app.processEvents()
assert view._widgets == before, "换主题应只重刷配色，不该重建控件"
settings.set_theme(original_theme)
window._apply_theme()
app.processEvents()
print("2 换主题: 只重刷配色")

# 3) 进度事件合并（400ms 一批），并且不必再 split("_", 1) 反解 user_id
for index in range(5):
    window._on_core_event("progress", {
        "key": KEY, "user_id": "u1", "nickname": "主播甲",
        "size": f"{index}.0 MB", "speed": "1.0 MB/s", "elapsed": f"00:0{index}",
        "bytes": index * 1024 * 1024})
assert "u1" not in view._records, "进度应先攒着，等定时器合并"
window._flush_progress()
app.processEvents()
assert "u1" in view._records, view._records
assert window._task_bytes[KEY] == 4 * 1024 * 1024
print("3 进度合并: 5 条并成 1 次刷新")

# 4) 底部合计：已录体积 + 磁盘剩余
window._update_counters()
total_text = view.total_label.text()
assert "已录 4.00 MB" in total_text and "磁盘剩余" in total_text, total_text
print(f"4 底部合计: {total_text}")

# 5) 开播提示 + 结束清理
window._on_core_event("started", {"key": KEY, "user_id": "u1", "nickname": "主播甲"})
assert window._task_cards.get(KEY) == "主播甲"
window._on_core_event("finished", {"key": KEY, "user_id": "u1", "nickname": "主播甲",
                                   "size": "4.00 MB", "duration": "00:04", "avg_speed": "1.0 MB/s"})
assert KEY not in window._task_cards and KEY not in window._task_bytes
assert "u1" not in view._records, view._records
print("5 开播/结束: 任务登记与界面同步")

# 6) 日志抽屉：折叠时缓冲，展开时补齐，错误计数出现在按钮上
window._toggle_log_panel(False)
buffered_before = len(window._pending_logs)   # 界面上的完成提示也走同一条日志通道
window._on_core_event("log", {"message": "取不到直播流地址", "level": "error"})
window._on_core_event("log", {"message": "断流后续录第 2 段", "level": "warn"})
assert not window.log_view.isVisible()
assert len(window._pending_logs) == buffered_before + 2, window._pending_logs
assert window._log_alerts == 2
assert "2 条提醒" in window.log_toggle.text(), window.log_toggle.text()
window.log_toggle.setChecked(True)      # 走真实交互路径
app.processEvents()
assert "取不到直播流地址" in window.log_view.toPlainText()
assert "断流后续录第 2 段" in window.log_view.toPlainText()
assert window.log_toggle.text() == "运行日志", window.log_toggle.text()
window.log_toggle.setChecked(False)
window._on_core_event("log", {"message": "关注列表已加载", "level": "info"})
assert len(window._pending_logs) == 1, window._pending_logs
print("6 日志抽屉: 折叠缓冲 / 展开补齐 / 提醒计数")

# 7) 日志上限由 document 自动裁剪（必须展开着测，否则行都进了折叠缓冲）
window.log_toggle.setChecked(True)
app.processEvents()
for index in range(main_window.LOG_PANEL_LINES + 40):
    window._on_core_event("log", {"message": f"第 {index} 条", "level": "info"})
app.processEvents()
assert window.log_view.document().blockCount() == main_window.LOG_PANEL_LINES, \
    window.log_view.document().blockCount()
assert f"第 {main_window.LOG_PANEL_LINES + 39} 条" in window.log_view.toPlainText()
assert "第 0 条" not in window.log_view.toPlainText()   # 丢的必须是最旧的
print(f"7 日志上限: {window.log_view.document().blockCount()} 行封顶，丢最旧")

# 8) 筛选 / 清除 / 键盘翻页
view.set_mode("list")
app.processEvents()
view.search_edit.setText("主播甲")
view._on_filter_changed()
app.processEvents()
assert view.filters_active()
assert len(view._widgets) == 1, view._widgets
view.clear_filters()
app.processEvents()
assert not view.filters_active() and len(view._widgets) == 2
view.size_combo.setCurrentText(view.size_combo.itemText(0))
view.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier))
app.processEvents()

# 8b) 监听筛选（空值 = 不筛）→ 悬浮 × 清除；表头点击排序与方向切换
assert view.monitor_combo.currentIndex() == -1 and view.monitor_combo.placeholderText() == "监听状态"
assert not view.filters_active(), "空值筛选下拉不该算作筛选条件"
view.monitor_combo.setCurrentIndex(0)        # 「自动」：只有 u1 开了自动录制
app.processEvents()
assert view.filters_active() and list(view._widgets) == ["u1"], view._widgets
combo = view.monitor_combo
spot = QPoint(int(combo.width() - combo.ARROW_MARGIN), combo.height() // 2)
assert combo._wants_clear(spot), "× 的命中区没落在箭头位置"
combo.mouseReleaseEvent(QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(spot),
                                    Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                                    Qt.KeyboardModifier.NoModifier))
app.processEvents()
assert combo.currentIndex() == -1 and not view.filters_active(), "点 × 没退回不筛"
# 下拉文字必须完整放得下：控件按最长项给宽，弹层再单独按最长项加宽
for box in (view.sort_combo, view.size_combo, view.monitor_combo):
    assert box.minimumWidth() >= box.contents_fit_width(), box.itemText(0)
    assert box.contents_fit_width() > box._widest_item_text(), "控件没给边框留位，文字会被裁掉"

view._users[0]["last_record"] = "2026-09-30 10:00:00"
view._users[1]["last_record"] = "2026-10-01 10:00:00"
head = view._list_heads["last"]
head.clicked.emit()                        # 点表头 = 按该列自然方向排序
app.processEvents()
assert view.sort_combo.currentText() == "最近录制时间" and view._sort_desc is True
assert "↓" in head.text(), head.text()
assert list(view._widgets) == ["u2", "u1"], view._widgets
head.clicked.emit()                        # 同一列再点一次换方向
app.processEvents()
assert view._sort_desc is False and "↑" in head.text(), head.text()
assert list(view._widgets) == ["u1", "u2"], view._widgets
view.sort_combo.setCurrentIndex(0)         # 回到默认排序：箭头收掉，列顺序不再受排序影响
app.processEvents()
assert "↑" not in head.text() and "↓" not in head.text(), head.text()
# 8c) 弹层高度：选中末项再弹出时一行都不能少，末行也得整条落进视口
#     （Qt 按样式表默认行高给弹层高度，比自绘委托的 24px 矮；项数少时它还留着替滚动条
#      划出的下边距，视口比视图还矮，末行胶囊的底边就压在边缘上）
for combo in (view.platform_combo, view.live_combo, view.record_combo, view.monitor_combo,
              view.sort_combo, view.size_combo, view.mode_combo):
    picked = combo.currentIndex()
    combo.setCurrentIndex(combo.count() - 1)      # 选中项落在最下面一行时最容易露馅
    label = combo.itemText(0)
    content = combo.count() * widgets.POPUP_ROW_HEIGHT
    for turn in (1, 2):                           # 第二次弹出：Qt 会把视图压回它自己算的高度
        combo.showPopup()
        # 首尾两行到卡片边的空位：赶在跑事件之前读，跟生产 _fit_popup_height 同一时机。
        # 事件一跑，Qt 会把容器那圈布局换掉，PySide 手里那个旧包装器就废了，再调一次
        # 直接抛「Internal C++ object already deleted」；量几何也不行，离屏下这一刻
        # 容器还没摆好（视图 y 能量出 -92）。
        pop_box = combo.view().parentWidget()
        margins = pop_box.layout().getContentsMargins()
        app.processEvents()
        pop = combo.view()
        assert pop.viewport().height() >= content, \
            f"{label} 弹层视口 {pop.viewport().height()}px，装不下 {content}px"
        last = pop.visualRect(pop.model().index(combo.count() - 1, 0))
        assert last.top() >= 0 and last.bottom() < pop.viewport().height(), \
            f"{label} 第 {turn} 次弹出末行没落进视口: {last}"
        assert pop.verticalScrollBar().value() == 0, "第一行被滚出可视区，下拉看着少一项"
        assert margins == (0, widgets.ArrowComboBox.POPUP_V_MARGIN, 0,
                           widgets.ArrowComboBox.POPUP_V_MARGIN), \
            f"{label} 容器上下留白 {margins}，左右必须为 0（卡片要和按钮同宽）"
        # 卡片描边到首末行那道白底：容器边距管不着，它得落在视图的视口边距上
        vm = pop.viewportMargins()
        assert (vm.top(), vm.bottom()) == (widgets.ArrowComboBox.POPUP_ROW_GAP,
                                           widgets.ArrowComboBox.POPUP_ROW_GAP) \
            and vm.left() == vm.right() == 0, \
            f"{label} 视口边距 {vm}，首末行会贴着卡片圆角"
        combo.hidePopup()
    combo.setCurrentIndex(picked)
print("8 筛选/清除/键盘翻页 + 悬浮×与表头排序 + 弹层高度与上下空位")

# 8d) 卡片 / 列表合并成一个下拉：工具栏不再有那两枚互斥按钮
assert not hasattr(view, "mode_buttons"), "两枚互斥按钮没拆干净"
assert view.mode_combo.currentText() == "列表", view.mode_combo.currentText()
view.mode_combo.setCurrentIndex(0)     # 相当于用户在下拉里选「卡片」
app.processEvents()
assert view._mode == "card" and view.stack.currentIndex() == 0, (view._mode, view.stack.currentIndex())
view.set_mode("list")                  # 外部直接切（启动恢复 / 别的入口），下拉要跟上
app.processEvents()
assert view.mode_combo.currentText() == "列表" and view.stack.currentIndex() == 1
print("8d 视图切换: 两枚按钮已合成一个下拉")

# 8e) 平台从纯展示改成真筛选：默认「全部平台」= 不筛，选具体平台也不能把人筛空
assert view.platform_combo.itemText(0) == "全部平台", view.platform_combo.itemText(0)
assert view.platform_combo.currentIndex() == 0 and not view.filters_active()
assert view.platform_combo.count() > 1, "平台下拉只剩一项，等于没改成筛选"
assert not view.platform_combo._clearable, "「全部平台」就是空值，别再叠一层悬浮 ×"
view.platform_combo.setCurrentIndex(1)         # 选「小红书」：现在的数据全来自它
app.processEvents()
assert view.filters_active() and len(view._widgets) == 2, \
    "按平台筛选把全量数据筛空了（用户条目没有 platform 字段时的默认值没兜住）"
view.clear_filters()
app.processEvents()
assert view.platform_combo.currentIndex() == 0 and not view.filters_active()
print("8e 平台筛选: 默认全部平台，选中平台走真筛选且不误伤全量")

# 8f) 弹层是与按钮同宽的圆角卡片；每页那档显示「N条/页」，真实值走 itemData
for combo in (view.platform_combo, view.live_combo, view.record_combo, view.monitor_combo,
              view.sort_combo, view.size_combo, view.mode_combo):
    combo.showPopup()
    app.processEvents()
    box = combo.view().window()
    assert box.width() == combo.width(), \
        f"弹层 {box.width()}px 与按钮 {combo.width()}px 不齐：{combo.itemText(0)}"
    # 容器窗必须带逐像素 alpha，否则圆角外那几像素上屏是黑的（黑影）。
    # 只有 WA_TranslucentBackground 不够，Windows 仍按不透明建窗；grab() 也量不出来，
    # 得靠真屏抓图比对，所以这里把两个条件都钉住，防止哪天删掉一个 flag。
    # 得靠真屏抓图比对，所以这里把三个条件都钉住，防止哪天删掉一个又回到黑影/黑线。
    # 投影那档必须用复数形式的 setWindowFlags 才会留在 windowFlags() 里，
    # setWindowFlag(NoDropShadow…) 看着设成功、实际那位被丢掉了。
    assert box.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground), "弹层容器没开透明背景"
    assert box.windowFlags() & Qt.WindowType.FramelessWindowHint, \
        "弹层容器缺 FramelessWindowHint：圆角外会上屏成黑影"
    assert box.windowFlags() & Qt.WindowType.NoDropShadowWindowHint, \
        "弹层没关系统投影：透明窗上它会画成贴着矩形右下的两条黑线"
    combo.hidePopup()
size = view.size_combo
assert [size.itemText(row) for row in range(size.count())] == \
    ["10条/页", "20条/页", "30条/页", "40条/页", "50条/页", "100条/页", "全部"], \
    [size.itemText(row) for row in range(size.count())]
assert [size.itemData(row) for row in range(size.count())] == \
    ["10", "20", "30", "40", "50", "100", "全部"], "文案改了就把落盘的值也带歪了"
saved_size = settings.current_page_size()
size.setCurrentIndex(2)                      # 「30条/页」
app.processEvents()
assert settings.current_page_size() == 30 and view._page_size() == 30
size.setCurrentIndex(size.count() - 1)       # 「全部」= 不分页
app.processEvents()
assert settings.current_page_size() == 0 and view._page_size() is None
settings.set_page_size(saved_size)
size.blockSignals(True)                      # 收尾复位不该再触发一次重建
size.setCurrentIndex(view._saved_size_index())
size.blockSignals(False)
print("8f 弹层同宽 + 每页「N条/页」")

# 8g) 空状态页：一份数据都没有时也不许留白，三种「没东西」分开说
# 这页以前只有 _rebuild 会切过去，而首屏没人调它 —— 默认列表模式下整个列表区是空的，
# 唯一的解释写在折叠着的日志抽屉里；切一次视图（走 _rebuild）它才「突然冒出来」，
# 于是看着像只有卡片模式才有。现在新建就站在这页上，两种模式共用同一张。
blank = following.FollowingView()


def _loading_hint(widget) -> bool:
    """「正在读取」那一档的字样：三枚点常驻，只有颜色轮着亮（见 FollowingView._hint_text）"""
    text = widget.empty_label.text()
    return text.startswith("正在读取关注列表") and text.count("·") == following.FollowingView.HINT_DOTS


assert blank.stack.currentIndex() == 2, "新建的视图停在卡片页：首屏整个列表区啥都没有"
assert _loading_hint(blank), blank.empty_label.text()
# 正在读的时候两枚按钮都没意义：重试的那次请求已经在飞，筛选要等数据回来才谈得上。
# 它们默认是「可见」的，不主动收着就会跟着首屏一起摆出来。
assert blank.empty_clear.isHidden() and blank.empty_refresh.isHidden(), "首屏摆出了两枚按钮"
blank.set_mode("list")
assert blank.stack.currentIndex() == 2, "切到列表模式空状态就掉了"
blank.resize(1100, 620)
blank.show()
app.processEvents()
_page = blank.stack.widget(2).geometry()
_center = blank.empty_label.geometry().center()
assert abs(_center.x() - _page.center().x()) <= 1, (_center, _page)   # 横向居中
assert _page.height() * 0.25 < _center.y() < _page.height() * 0.75, (_center, _page)
blank.set_loading(True)
assert blank.stack.currentIndex() == 2 and _loading_hint(blank)
assert blank.empty_clear.isHidden() and blank.empty_refresh.isHidden(), "重新读取时按钮跟着冒出来"
# 省略号动画：每拍字数必须一样，不然居中的那一行会跟着左右晃
assert blank._hint_timer.isActive(), "在读却什么都没转"
_step0 = blank.empty_label.text()
blank._hint_step = 1
blank._advance_hint()
assert blank.empty_label.text() != _step0 and _loading_hint(blank), "换拍换了字数（行会晃）"
assert blank._hint_step == 2
blank._hint_step = 3
blank._advance_hint()
assert blank._hint_step == 1, "转完一轮没回到第一枚"
# 那三枚点的颜色是当场解析成字符串写进文字的，不吃 QSS 换肤：换主题时必须重涂一次
_theme_before = settings.current_theme()
settings.set_theme("dark" if _theme_before != "dark" else "light")
_stale = blank.empty_label.text()
blank.refresh_theme()
assert blank.empty_label.text() != _stale, "换主题后省略号还留着旧配色"
settings.set_theme(_theme_before)
blank.refresh_theme()
blank.set_loading(False)          # 失败路径只说「不读了」，没人再改那行字
assert blank.empty_label.text().startswith("没读到关注用户"), \
    "读取失败后还挂着「正在读取…」骗人在转"
assert blank.stack.currentIndex() == 2, "改口时顺手把空状态页切走了"
assert blank.empty_clear.isHidden(), "一个筛选都没开，却给出「清除筛选」出口"
assert not blank.empty_refresh.isHidden(), "读空了连重试的出口都没了"
assert not blank._hint_timer.isActive(), "已经改口了还在转动画"
blank.set_users([{"user_id": "u1", "nickname": "甲"}])
assert blank.stack.currentIndex() == 1, "有数据了还压在空状态页上"
blank.search_edit.setText("对不上的关键词")
blank._rebuild()
assert blank.stack.currentIndex() == 2 and \
    blank.empty_label.text() == "没有符合条件的关注用户", blank.empty_label.text()
assert not blank.empty_clear.isHidden(), "筛没了却不给「清除筛选」出口"
assert not blank.empty_refresh.isHidden(), "筛没了把重试也一并收了"
blank.clear_filters()
assert blank.stack.currentIndex() == 1, "点清除没把人带回来"
# 手上有数据时点「刷新」：列表该原地留着，不该闪成一张空页（旧版会整页翻白再翻回来）
blank.set_users([{"user_id": "u1", "nickname": "甲"}])
blank.set_loading(True)
assert blank.stack.currentIndex() == 1, "刷新把已有列表换成了「正在读取」那一页"
blank.set_loading(False)
assert blank.stack.currentIndex() == 1
blank.close()
print("8g 空状态页: 首屏不空白、两种视图共用、正在读取/没读到/筛没了三档分开")

# 9) 「24」是只读标签：开了自动录制才挂出来，改状态只有「···」菜单一个入口
profiles.set_auto_record = lambda user_id, nickname, enabled: None   # 不写用户档案
view.set_mode("card")
app.processEvents()
card = view._widgets["u1"]
tag = card.auto_tag
assert tag.isVisible() and tag.width() == tag.SIZE, tag.size()
assert not hasattr(tag, "clicked"), "「24」还留着点击信号，没退成标签"
assert tag.focusPolicy() == Qt.FocusPolicy.NoFocus, "标签还在抢 Tab 焦点"

def toggle_via_menu(caption: str) -> None:
    """点「···」菜单里的自动录制项：视图 → 主窗口 → 回写卡片，一整条链路"""
    menu = view._build_menu(card.user())
    texts = [action.text() for action in menu.actions()]
    assert caption in texts, texts
    menu.actions()[texts.index(caption)].trigger()

toggle_via_menu("关闭自动录制")
app.processEvents()
assert card.user().get("auto_record") is False and not card.auto_tag.isVisible(), \
    "菜单关了自动录制，「24」标签没收掉"
toggle_via_menu("开启自动录制")
app.processEvents()
assert card.user().get("auto_record") is True and card.auto_tag.isVisible(), \
    "菜单开了自动录制，「24」标签没挂出来"
print("9 自动录制: 「24」只读，开关只在「···」菜单里")

# 9b) 录制中的圆点扩散圈：只在「在录 + 看得见」时转
card.cover.set_record({"size": "1.0 MB", "speed": "1 MB/s", "elapsed": "00:00:05"})
assert card.cover.isVisible(), "卡片没显示出来，动画断言没意义"
assert card.cover._ripple.isActive(), "录制中没起扩散圈动画"
card.cover.set_record(None)
assert not card.cover._ripple.isActive(), "停录后扩散圈还在空转"
print("9b 录制圆点动画: 起于录制、停于结束")

# 9c) 卡片整层不再有悬浮提示：备注 / 录制信息改由封面左上角标题与状态条直给
card.set_auto_record(False)
assert card.toolTip() == "", f"卡片又长出悬浮提示：{card.toolTip()!r}"
assert card.auto_tag.toolTip().startswith("自动录制"), "标签自身的说明不该被一起删掉"
card.set_auto_record(True)
print("9c 卡片悬浮提示: 整层已删，控件级说明保留")

# 9c2) 封面那行字只认直播标题：主播没填就留空，不拿简介顶（试过，当天改回 —— 见 cover_caption）
base = {"user_id": "u1", "nickname": "主播甲", "room_id": "100", "is_live": True,
        "avatar": "", "auto_record": True, "video_url": "", "last_record": ""}
card.cover.sync_user({**base, "title": "深夜电台", "desc": "记录生活"})
assert card.cover.cover_caption() == "深夜电台"
card.cover.sync_user({**base, "title": "", "desc": "记录生活"})
assert card.cover.cover_caption() == "", "空标题拿简介顶上了：简介不该出现在这一格"
card.cover.sync_user({**base, "title": "   ", "desc": ""})
assert card.cover.cover_caption() == "", "全是空白也该当没有"
card.cover.sync_user({**base, "is_live": False, "title": "深夜电台", "desc": "记录生活"})
assert card.cover.cover_caption() == "", "没开播不该在封面上留字"
print("9c2 封面标题: 只认直播标题，没填就留空")

# 9c3) 列表行的「录制中」胶囊与标题栏同轴：绿 =「我在产出」，红只留给出事
#      （原来它是品牌红 accent_soft，浅底上和 err 红肉眼分不开，还挨着绿色的「直播中」）
view.set_mode("list")
app.processEvents()
row = view._widgets["u1"]
row.set_record({"size": "1.0 MB", "speed": "1 MB/s", "elapsed": "00:00:05"})
record_style = row.record_label.styleSheet()
assert theme._rgb("ok") in record_style, f"录制中该是绿（在产出）：{record_style}"
assert "accent" not in record_style, f"品牌红又回来兼任状态色了：{record_style}"
row.set_record(None)
assert row.record_label.text() == "空闲"
view.set_mode("card")
app.processEvents()
card = view._widgets["u1"]   # 换模式重建控件，后面几项还要用卡片
assert card.cover is not None
print("9c3 列表录制胶囊: 绿=在产出，品牌红退出状态语义")

# 9c4) 底部「录制中 N」必须跟着录制起止走：以前只有整页重建时才重算，
#      于是标题栏写着「录制中 · 2 路」，脚边那行还停在 0（真机截图抓到的）
view.update_record("u1", "1.0 MB", "1 MB/s", "00:00:05(5.0s)")
app.processEvents()
assert "录制中 1" in view.count_label.text(), view.count_label.text()
view.update_record("u2", "2.0 MB", "1 MB/s", "00:00:09(9.0s)")
assert "录制中 2" in view.count_label.text(), view.count_label.text()
view.clear_record("u2")
assert "录制中 1" in view.count_label.text(), view.count_label.text()
view.clear_record("u1")
assert "录制中 0" in view.count_label.text(), view.count_label.text()
print("9c4 底部计数: 录制起止当场跟上，不会停在旧数")

# 9d) 「···」挪到卡片右下角；右键卡片开同一个菜单；菜单按三组分项
more_top_left = card.more.mapTo(card, QPoint(0, 0))
assert more_top_left.x() > card.width() // 2 and more_top_left.y() > card.height() // 2, \
    f"「···」还吊在右上角: {more_top_left} / 卡片 {card.width()}x{card.height()}"
assert card.height() - (more_top_left.y() + card.more.height()) <= 12, "没贴到卡片底部"
# 单独一个 objectName：卡片这枚是无边框纯点，标题栏那组仍带描边，不能共用样式
assert card.more.objectName() == "moreBtn", card.more.objectName()
assert "QPushButton#moreBtn" in theme.theme_qss(), "样式表里缺 moreBtn 的无边框规则"
seen = []
view._show_menu = lambda user, anchor: seen.append(user.get("user_id"))   # 弹出归事件循环管，测试里先顶掉
card.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QCursor.pos()))
del view._show_menu
assert seen == ["u1"], "右键卡片没走「更多操作」菜单"
captions = [action.text() for action in view._build_menu(card.user()).actions()]
assert captions == ["开始录制", "", "打开录制目录", "复制房间号", "实时预览", "",
                    "关闭自动录制"], captions
print("9d 卡片「···」: 右下角无边框纯点，右键开同一个三组菜单")

# 9d2) 「实时预览」这一项：能点时要能点、不能点时要把原因写在悬浮提示上（只置灰不解释
#      最容易被当成 bug），正在录制则提示它多占一路拉流；窗口已经开着就换成「停止预览」。
#      这里只验菜单状态，真起 ffplay 由核心层那项和外部探针验，测试绝不碰网络。
#      用显式字典而不是 card.user()：前面几项把 USERS[0]["is_live"] 改成过 False 了。
saved_ffplay, saved_is_open = config.FFPLAY_PATH, preview.is_open
config.FFPLAY_PATH = r"C:\ffmpeg\bin\ffplay.exe"      # 不依赖测试机装没装
on_air = dict(USERS[0], is_live=True, video_url="http://x/flv")
try:
    items = {a.text(): a for a in view._build_menu(on_air).actions() if a.text()}
    assert "实时预览" in items, list(items)
    # Qt6 的 QAction 没设 tooltip 时会拿 text() 顶上，所以这里只能验「没有否决理由」
    assert items["实时预览"].isEnabled() and "未开播" not in items["实时预览"].toolTip(), \
        f"开播且没有录制时该直接可点：「{items['实时预览'].toolTip()}」"
    view.update_record("u1", "1.0 MB", "1 MB/s", "00:00:05(5.0s)")
    items = {a.text(): a for a in view._build_menu(on_air).actions() if a.text()}
    assert "录制中" in items["实时预览"].toolTip(), items["实时预览"].toolTip()
    view.clear_record("u1")
    # 未开播的 u2：置灰 + 说明
    offline = dict(USERS[1], is_live=False, video_url="")
    items = {a.text(): a for a in view._build_menu(offline).actions() if a.text()}
    assert not items["实时预览"].isEnabled() and "未开播" in items["实时预览"].toolTip(), \
        items["实时预览"].toolTip()
    items = {a.text(): a for a in view._build_menu(dict(offline, is_live=True,
                                                        video_url="")).actions() if a.text()}
    assert "没有直播流地址" in items["实时预览"].toolTip(), items["实时预览"].toolTip()
    # 缺 ffplay：整项写清楚为什么不存在预览
    config.FFPLAY_PATH = None
    items = {a.text(): a for a in view._build_menu(on_air).actions() if a.text()}
    assert not items["实时预览"].isEnabled() and "ffplay" in items["实时预览"].toolTip()
    config.FFPLAY_PATH = r"C:\ffmpeg\bin\ffplay.exe"
    preview.is_open = lambda user_id: True
    items = {a.text(): a for a in view._build_menu(on_air).actions() if a.text()}
    assert "停止预览" in items and "实时预览" not in items, list(items)
    # 菜单项得真接到主窗口的 handler：这里只验路由和回话，真起进程由核心层那项负责
    saved_open, saved_close = preview_window.open_card, preview_window.close_card
    routed = []
    preview_window.open_card = lambda user, host: routed.append(("open", user.get("user_id"))) or "[主播甲] 预览已打开"
    preview_window.close_card = lambda user_id: routed.append(("close", user_id)) or False
    try:
        window._on_following_action("preview", {"user_id": "u1", "nickname": "主播甲"})
        window._on_following_action("preview_stop", {"user_id": "u1", "nickname": "主播甲"})
    finally:
        preview_window.open_card, preview_window.close_card = saved_open, saved_close
    assert routed == [("open", "u1"), ("close", "u1")], routed
    assert "[主播甲] 预览已打开" in window.log_view.toPlainText(), "预览的回话没进日志"
finally:
    config.FFPLAY_PATH = saved_ffplay
    preview.is_open = saved_is_open
    view.clear_record("u1")
print("9d2 菜单「实时预览」: 置灰带原因、录制中提示多一路、开着就改「停止预览」")

# 9d3) 预览卡片：ffplay 那画面本身吃不了主题，边框、标题栏、拖动都由这张自绘卡片包出来。
#      离屏没有真窗口可嵌，所以这里验几何、标题栏、退出联动；上屏效果归真屏探针。
saved_open_preview, saved_find = preview.open_preview, preview.find_hwnd
saved_close, saved_is_open = preview.close, preview.is_open
saved_region = preview.set_round_region
calls = []
regions = []
try:
    preview.find_hwnd = lambda user_id: 0       # 离屏没有 SDL 窗口，卡片停在暗底那一步
    preview.close = lambda user_id: calls.append(("close", str(user_id))) or str(user_id) == "u1"
    preview.open_preview = (lambda user, size=None:
                            calls.append(("open", size)) or f"[{user['nickname']}] 预览已打开")
    preview.is_open = lambda user_id: True      # open_card 靠它决定要不要摆卡片
    # 离屏那个 winId 不是真句柄，别拿它去调用 Win32；只记下我们交给它的尺寸
    preview.set_round_region = (lambda hwnd, w, h, r: regions.append((w, h, r)) or True)

    host_h = window.height()
    notice = preview_window.open_card({"user_id": "u1", "nickname": "主播甲"}, window)
    assert notice.startswith("[主播甲]"), notice
    assert preview_window.is_open("u1") and len(preview_window._cards) == 1
    pcard = preview_window._cards["u1"]

    width, height = preview_window.card_size(host_h)
    assert (pcard.width(), pcard.height()) == (width, height), (pcard.width(), pcard.height())
    assert height == host_h - 2 * preview_window.EDGE, "上下各让一档，顶满屏幕四角就跑到屏外了"
    assert width == round(height * 9 / 16), "宽按竖屏画幅算，不是随手给的一个数"
    dpr = pcard.devicePixelRatioF()
    x, y, content_w, content_h = pcard.content_geometry()
    assert x == round(preview_window.PAD * dpr), x
    assert y == round((preview_window.PAD + preview_window.HEADER) * dpr), y
    # 摆在主窗口正中：预览是来看这一路画面的，摆边上等于让人扭头看
    # （放不下时会被夹进屏幕可用区，所以这里留几像素余量，判的是「大致居中」而不是偏出去几百）
    mid = pcard.geometry().center()
    want = window.geometry().center()
    assert abs(mid.x() - want.x()) <= 4 and abs(mid.y() - want.y()) <= 4, (mid, want)
    # 卡片挂在主窗口下当「附属顶层窗」（和设置窗同一套）：压住主窗口靠的是这层父子关系，
    # 真屏量过 —— 不挂 parent 时点一下主窗口，预览就被盖到后面去了
    assert pcard.parent() is window, "卡片没挂到主窗口下，点主窗口就把它盖后面了"
    assert pcard.isWindow(), "挂了 parent 还得留着 Qt.Window 标志，否则就成了主窗口里的子控件"
    # 卡片必须是不透明窗口：逐像素透明（layered window）会让嵌进来的原生子窗口整个不画，
    # 画面直接变黑块。圆角靠窗口的裁剪区域，而区域只能按设备像素上（真屏量过：Qt 的 setMask
    # 只有逻辑像素精度，200% 缩放时每一档台阶是 2 物理像素，12 半径的圆弧只剩 7 档）
    assert not pcard.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert pcard.mask().isEmpty(), "设了 Qt 遮罩就会把设备像素那份区域顶掉，角又变回粗台阶"
    assert regions, "卡片没上裁剪区域，四角就是方的"
    saved_dpr = pcard.devicePixelRatioF
    pcard.devicePixelRatioF = lambda: 2.0       # 用户那台机器就是 200% 缩放
    pcard._apply_region()
    pcard.devicePixelRatioF = saved_dpr
    assert regions[-1] == (width * 2, height * 2, preview_window.RADIUS * 2), regions[-1]
    assert calls[0] == ("open", (content_w, content_h)), calls   # 起进程时就把内容区尺寸给了它
    assert round(pcard.width() * dpr) - (x + content_w) == x, "画面左右留白不等就歪了"
    assert x + content_w <= round(pcard.width() * dpr) and y + content_h <= round(pcard.height() * dpr)

    assert pcard.findChildren(QLabel)[0].text() == "主播甲", "标题栏写主播全名，不省略"
    close_btn = pcard.findChildren(QPushButton)[0]
    assert close_btn.text() == theme.CLOSE_GLYPH, close_btn.text()
    preview_window.refresh_all()

    class _At:
        def __init__(self, point): self._point = QPointF(point)
        def globalPosition(self): return self._point

    def press_at(local_y: int) -> _At:
        return _At(pcard.mapToGlobal(QPoint(0, local_y)))

    assert pcard._in_header(press_at(preview_window.PAD + 1)), "标题行按得住才拖得动"
    assert not pcard._in_header(press_at(pcard.height() - 1)), "画面那一带不该抢鼠标"

    again = preview_window.open_card({"user_id": "u1", "nickname": "主播甲"}, window)
    assert "已经开着" in again and len(preview_window._cards) == 1, again

    close_btn.click()
    assert not preview_window.is_open("u1"), "点了叉卡片还挂着，菜单就永远停在「停止预览」"
    assert ("close", "u1") in calls, calls
    # 卡片挂在主窗口下才有「压在主窗口之上」，所以关掉时必须自己从父子关系里摘出去
    assert not window.findChildren(preview_window.PreviewCard), \
        "关掉的卡片还被主窗口牵着，开几轮就攒出一堆看不见的僵尸窗口"
    assert preview_window.close_card("u404") is False
    assert preview_window.close_all() == 0
finally:
    preview.open_preview, preview.find_hwnd = saved_open_preview, saved_find
    preview.close, preview.is_open = saved_close, saved_is_open
    preview.set_round_region = saved_region
    preview_window.close_all()
print("9d3 预览卡片: 9:16 与主窗口等高、标题栏带主播名、✕ 一并收进程")

# 9d4) 嵌入的先后顺序：等 ffplay 自己把窗口开成 -x/-y 那一档之后才认爹，而且只挪位
#      替它改尺寸 = 它的渲染视口还停在旧尺寸上，画面会被放大着画出去、右边切掉一截
saved_api = (preview.find_hwnd, preview.window_size, preview.set_visible,
             preview.adopt, preview.embed, preview.move, preview.set_round_region)
try:
    steps, sized_to = [], [0, 0]
    preview.find_hwnd = lambda user_id: 4321
    preview.window_size = lambda hwnd: tuple(sized_to)
    preview.set_visible = lambda hwnd, visible: steps.append(("visible", visible))
    preview.adopt = lambda parent, hwnd, x, y: steps.append(("adopt", x, y)) or True
    preview.embed = lambda parent, hwnd, x, y, w, h: steps.append(("embed", w, h)) or True
    preview.move = lambda hwnd, x, y: steps.append(("move", x, y)) or True
    preview.set_round_region = lambda hwnd, w, h, r: True

    wcard = preview_window.PreviewCard({"user_id": "u7", "nickname": "主播丁"}, window)
    wcard.show()        # 等待靠 showEvent 起表；脚本里没有事件循环，表不会自己走
    cx, cy, cw, ch = wcard.content_geometry()
    wcard._attach()
    assert steps == [("visible", False)], f"拿到句柄第一步该先把它藏起来: {steps}"
    assert wcard.child_hwnd == 4321 and wcard._poll.isActive()
    steps.clear()
    wcard._attach()
    assert steps == [], "它自己还没把窗口开大，我们不能抢这一步"
    sized_to[:] = [cw, ch]
    wcard._attach()
    assert not any(s[0] == "embed" for s in steps), f"尺寸已经对了，不该再替它改: {steps}"
    assert ("adopt", cx, cy) in steps and steps[-1] == ("visible", True), steps
    assert not wcard._poll.isActive(), "嵌好了就不必再等它开大"
    steps.clear()
    wcard._reassert()
    assert steps == [("move", cx, cy)], "头几秒按回原位也只能挪位"
    wcard.close()

    sized_to[:] = [640, 480]          # 它一直不改成我们要的那一档
    steps.clear()
    fcard = preview_window.PreviewCard({"user_id": "u8", "nickname": "主播戊"}, window)
    fcard._attach()
    fcard._waits_left = 1
    fcard._attach()
    assert any(s[0] == "embed" for s in steps), "等过头得硬摆一挡，别给一张永远空着的卡片"
    assert steps[-1] == ("visible", True), steps
    fcard.close()
finally:
    (preview.find_hwnd, preview.window_size, preview.set_visible,
     preview.adopt, preview.embed, preview.move,
     preview.set_round_region) = saved_api
    preview_window.close_all()
print("9d4 嵌入顺序: 等它自己开大尺寸、我们只挪位，等过头才硬摆")

# 9e) 「···」菜单是气泡菜单：尖角指着那三个点，屏幕右边放不下就翻到左边开
area = QApplication.primaryScreen().availableGeometry()


def anchor_at(x: int, y: int) -> QWidget:
    """一个跟「···」同尺寸的假锚点，摆在指定屏幕位置上当弹出参照"""
    anchor = QWidget()
    anchor.setFixedSize(22, 22)
    anchor.move(x, y)
    anchor.show()
    for _ in range(4):
        app.processEvents()
    return anchor


menu = view._build_menu(card.user())
assert isinstance(menu, widgets.CalloutMenu), type(menu).__name__
assert menu.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground), "气泡菜单没开透明底"
# 少了这个 flag，Qt::Popup 仍按不透明建窗：尖角旁边那条空位上屏是黑的
assert menu.windowFlags() & Qt.WindowType.FramelessWindowHint, "菜单缺 FramelessWindowHint"
assert menu.windowFlags() & Qt.WindowType.NoDropShadowWindowHint, \
    "菜单没关系统投影：透明窗上它会画成贴着矩形右下的两条黑线"
assert menu.testAttribute(Qt.WidgetAttribute.WA_DeleteOnClose), "popup 不阻塞，关掉的菜单得自己收掉"
anchor = anchor_at(area.left() + 120, area.top() + 140)
widgets.show_callout_menu(menu, anchor)
app.processEvents()
assert menu.points_left(), "空间够时尖角该在左边（菜单开在锚点右侧）"
assert menu.x() >= anchor.mapToGlobal(QPoint(0, 0)).x() + anchor.width(), "菜单压到锚点上了"
center_y = anchor.mapToGlobal(QPoint(0, 0)).y() + anchor.height() // 2
assert menu.y() + menu.arrow_y() == center_y, \
    f"尖角没对准按钮中线: 菜单 y={menu.y()} 尖角 {menu.arrow_y()} 中线 {center_y}"
menu.close()
menu = view._build_menu(card.user())
anchor_right = anchor_at(area.right() - 20, area.top() + 140)
widgets.show_callout_menu(menu, anchor_right)
app.processEvents()
assert not menu.points_left(), "贴着屏幕右边缘还往右开，菜单要出界"
assert menu.x() + menu.width() - 1 <= area.right(), f"翻边后仍出界: {menu.x()}+{menu.width()}"
assert menu.y() >= area.top() and menu.y() + menu.height() - 1 <= area.bottom(), \
    f"菜单上下出界: y={menu.y()} 高 {menu.height()} 可用区 {area.top()}..{area.bottom()}"
menu.close()
anchor.deleteLater()
anchor_right.deleteLater()
print("9e 气泡菜单: 尖角对准「···」，右边缘自动翻边且不越屏")

# 9f) 悬浮提示：样式表里 QToolTip 的圆角要真圆，得先把提示窗改成逐像素透明
theme.install_rounded_tooltips(app)   # 生产在 main.py 里装，这里走同一条路
QToolTip.showText(view.sort_combo.mapToGlobal(QPoint(10, 10)), "排序方式", view.sort_combo)
for _ in range(6):
    app.processEvents()
tip_label = next((w for w in QApplication.topLevelWidgets()
                  if w.objectName() == "qtooltip_label"), None)
assert tip_label is not None, "找不到全局提示窗：类名/对象名一改，过滤器就静默失效了"
assert tip_label.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground), \
    "提示窗仍按不透明建窗：圆角外那四个方块会上屏成系统底色的「角」"
QToolTip.hideText()
print("9f 悬浮提示: 提示窗改透明底，四个圆角不再露方角")

# 9g) 下拉弹层不带展开动画：那动画靠一格格撑高窗口来画，撑出来的像素还没画过，上屏是黑的
theme.disable_combo_roll(app)          # 生产在 main.py 里调，这里走同一条路
assert not app.isEffectEnabled(Qt.UIEffect.UI_AnimateCombo), \
    "下拉展开动画还开着：卡片下沿会拖着一条扫下来的黑影"
print("9g 下拉弹层: 展开动画关掉，不再有跟着扫下来的黑边")

# 9h) 退出确认框：去掉系统标题栏后圆角、投影、居中、按钮接线全得自己负责
box = widgets._ConfirmDialog(window, "还有录制在进行", "正文", "退出并保存", "继续录制")
flags = box.windowFlags()
assert flags & Qt.WindowType.FramelessWindowHint, "还挂着系统标题栏：圆角卡片没地方画"
assert flags & Qt.WindowType.NoDropShadowWindowHint, \
    "系统投影没关：逐像素透明的窗上它只画成贴着矩形右下一条硬黑边"
assert box.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground), \
    "仍按不透明建窗：卡片外那圈手画投影会糊成一整块黑"
assert box.isModal(), "确认框不模态：还能点到主窗，退出流程要打架"
assert box.ok.isDefault(), "主操作没设成默认按钮：回车按不到「退出并保存」"
box.ok.click()
assert box.result() == QDialog.DialogCode.Accepted, "「退出并保存」没接到 accept"
box.cancel.click()
assert box.result() == QDialog.DialogCode.Rejected, "「继续录制」没接到 reject"
box.close_btn.click()
assert box.result() == QDialog.DialogCode.Rejected, "右上角关闭键没接到 reject"
assert box.close_btn.text() == theme.CLOSE_GLYPH, "确认框的关闭键没跟主界面同字形"
box.show()
app.processEvents()
offset = (box.geometry().center() - window.geometry().center())
assert max(abs(offset.x()), abs(offset.y())) <= 1, \
    f"确认框没摆在主窗正中（系统标题栏没了，Qt 的默认摆放不管事）: {offset.x()}, {offset.y()}"
# 两枚按钮等高、红底那枚左右留白够：全局样式表里 primaryBtn 压根没写 padding，
# 靠控件自带的同名规则顶回来 —— 这条规则哪天不生效了，红按钮会矮一截且字贴圆角
assert box.ok.height() == box.cancel.height(), \
    f"两枚按钮不等高 {box.ok.height()}vs{box.cancel.height()}：primaryBtn 的 padding 没顶掉全局样式"
assert box.ok.width() - box.ok.fontMetrics().horizontalAdvance("退出并保存") >= 24, \
    "主按钮左右留白不足，文字会挤到圆角上"
box.close()
box.deleteLater()
# confirm() 只把 exec 的结果换成布尔：两档都得对得上
_real_exec = widgets._ConfirmDialog.exec


def _fake_exec(answer):
    def run(self):
        (self.ok if answer else self.cancel).click()
        return int(QDialog.DialogCode.Accepted if answer else QDialog.DialogCode.Rejected)
    return run


widgets._ConfirmDialog.exec = _fake_exec(True)
assert widgets.confirm(window, "标题", "正文", "确定", "取消") is True, "确定档没返回 True"
widgets._ConfirmDialog.exec = _fake_exec(False)
assert widgets.confirm(window, "标题", "正文", "确定", "取消") is False, "取消档没返回 False"
widgets._ConfirmDialog.exec = _real_exec
print("9h 退出确认框: 透明建窗 + 模态 + 默认按钮 + 三处出口接线")

# 9i) 普通弹出菜单：跟气泡菜单共用同一套圆角卡片，别再是实心方角
def _rounded_window(menu) -> bool:
    flags = menu.windowFlags()
    return bool(flags & Qt.WindowType.FramelessWindowHint
                and flags & Qt.WindowType.NoDropShadowWindowHint
                and menu.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))


plain = widgets.RoundedMenu(window)
assert _rounded_window(plain), "RoundedMenu 没走透明建窗：圆角外那圈会上屏成黑"
assert "background: transparent" in plain.styleSheet(), \
    "没顶掉应用级 QMenu 底色：透明窗上样式表那块底就是一张黑卡片"
# 离屏没有系统托盘，_init_tray 整段被跳过（QSystemTrayIcon 建不出来），
# 托盘菜单那条只能看真屏探针；这里查同一条构造路径上的两个入口
opened = []
_real_menu_exec = widgets.RoundedMenu.exec
widgets.RoundedMenu.exec = lambda self, *args: (opened.append(self), 0)[1]
view._show_fields_menu()
widgets.RoundedMenu.exec = _real_menu_exec
assert opened and _rounded_window(opened[0]), "「显示字段」面板没走 RoundedMenu"
callout = widgets.CalloutMenu()
assert isinstance(callout, widgets.RoundedMenu) and _rounded_window(callout), \
    "气泡菜单与圆角菜单不再是同一套绘制，改卡片样式会只改到一半"
assert callout.styleSheet() != plain.styleSheet(), "气泡菜单的不对称 padding 被基类覆盖了"
callout.deleteLater()
plain.deleteLater()
print("9i 显示字段面板/气泡菜单: 共用同一套圆角卡片（托盘菜单见真屏探针）")

# 10) 最大化：边距收紧且不越过可用区域
screen = QApplication.primaryScreen()
if screen is not None:
    area = screen.availableGeometry()
    window.showMaximized()
    app.processEvents()
    assert window._root_layout.contentsMargins().left() <= 8
    assert window.height() <= area.height() + 2, (window.height(), area.height())
    window.showNormal()
    app.processEvents()
    assert window._root_layout.contentsMargins().left() == 14
print("10 最大化: 边距收紧、不盖任务栏")

# 10b) 标题栏的最大化按钮：点它进出最大化，字形跟着窗口状态换
if screen is not None:
    bar = window.title_bar
    normal = window.geometry().size()
    assert bar.btn_min.x() < bar.btn_max.x() < bar.btn_close.x(), \
        "窗口按钮不是「最小化 / 最大化 / 关闭」的顺序"
    assert bar.btn_max.text() == theme.MAXIMIZE_GLYPH, "没最大化时按钮不该是还原字形"
    bar.btn_max.click()
    app.processEvents()
    assert window.isMaximized(), "点最大化按钮没进最大化"
    assert bar.btn_max.text() == theme.RESTORE_GLYPH and "还原" in bar.btn_max.toolTip(), \
        "字形/提示没跟着窗口状态换"
    assert window.geometry().height() <= area.height() + 2, window.geometry()
    bar.btn_max.click()
    app.processEvents()
    assert not window.isMaximized(), "点还原按钮没退出最大化"
    assert window.geometry().size() == normal, (window.geometry().size(), normal)
    assert bar.btn_max.text() == theme.MAXIMIZE_GLYPH
    # 不走按钮的入口（Win+↑、双击标题栏）也得把字形带回来
    window.showMaximized()
    app.processEvents()
    assert bar.btn_max.text() == theme.RESTORE_GLYPH, "状态变了字形没变"
    window.showNormal()
    app.processEvents()
print("10b 标题栏最大化按钮: 点得动、字形跟着状态换、还原后尺寸回原样")

# 10c) 标题栏：状态胶囊六档、版本号那枚小字、窗口控制与功能按钮的层级分开
bar = window.title_bar
bar.set_status(True, False, 0)
assert bar.status_label.text() == "监控中", bar.status_label.text()
# 三档三色：出事红 / 在录绿 / 只是开着蓝（原先录制走品牌红、监控走绿，两档看着都在「正常干活」）
assert bar.status_label.styleSheet().startswith(f"color:{theme._rgb('info')}"), \
    "监控中该是蓝：它既不危险也不产出"
bar.set_status(True, False, 2)
assert bar.status_label.text() == "录制中 · 2 路", "在录路数没顶掉「监控中」"
assert bar.status_label.styleSheet().startswith(f"color:{theme._rgb('ok')}"), \
    "录制中该是绿：这才是「在出活」那一档"
bar.set_status(True, True, 2)
assert bar.status_label.text() == "停止中", "停止中该盖过录制中"
window._task_cards["测试_1"] = "测试主播"
window._update_counters()
assert bar.status_label.text() == "录制中 · 1 路", "开录没带动胶囊：窗口被盖住时它唯一在说话"
window._task_cards.clear()
bar.set_status(False, False, 0)
assert bar.status_label.text() == "未启动"
# 登录态失效是第五档：它盖过「录制中」但不吞掉路数。sid 死了只是发现不了新开播，
# 在录的流照旧在跑；只写「失效」会让人以为程序停摆了而直接退出，反而掐掉在录的几路。
window._task_cards["测试_2"] = "测试主播2"
bar.set_status(True, False, 1)
_balloons = []
_real_notify = window._notify_tray
window._notify_tray = lambda title, message: _balloons.append((title, message))
try:
    window._on_auth_state(True, "服务端说：登录已过期")
    assert bar.status_label.text() == "登录态失效 · 在录 1 路", bar.status_label.text()
    assert bar.status_label.styleSheet().startswith(f"color:{theme._rgb('err')}"), \
        "失效档没变红：用户扫一眼看不出出事了"
    assert "设置 → 登录态" in bar.status_label.parentWidget().toolTip(), \
        "红灯不指路，用户不知道该干什么"
    assert len(_balloons) == 1, _balloons
    window._on_auth_state(True, "服务端说：登录已过期")   # 轮询每 30 秒重报一次
    assert len(_balloons) == 1, "重复上报又弹了一次托盘"
    window._on_auth_state(False, "")
    assert bar.status_label.text() == "录制中 · 1 路", "登录态恢复后没回到原来那一档"
    assert bar.status_label.parentWidget().toolTip() == "", "提示没跟着清掉"
    window._on_auth_state(True, "还没有配置登录态 sid")
    window._on_auth_state(False, "")                     # 恢复 → 再失效，托盘该重新弹
    assert len(_balloons) == 2, _balloons
    assert bar.status_label.text() == "录制中 · 1 路"     # 最后一档已恢复
finally:
    window._notify_tray = _real_notify
    window._auth_expired = False
    bar.set_auth_expired(False)
    window._task_cards.clear()
    bar.set_status(False, False, 0)
# 缺 ffmpeg 是又一档红，而且盖在登录态失效前面：失效在设置里重新粘贴 sid 就能修好，
# 缺工具则整条录制链路都不出活，修 sid 也救不回来 —— 先说没有解的那一件。
# 它没有 setter（FFMPEG_PATH 在 import 时就定死了），所以改全局再重画一次来证明这档真的存在。
_real_ffmpeg = config.FFMPEG_PATH
try:
    config.FFMPEG_PATH = None
    bar.refresh_theme()
    assert bar.status_label.text() == "缺少 ffmpeg", bar.status_label.text()
    assert bar.status_label.styleSheet().startswith(f"color:{theme._rgb('err')}"), \
        "缺工具没走红档：这台机器录不出任何东西，却看着像只是没启动"
    _tip = bar.status_label.parentWidget().toolTip()
    assert "PATH" in _tip and "重启" in _tip, _tip   # 红完要指路，还得说明改文件得重启
    bar.set_auth_expired(True)
    assert bar.status_label.text() == "缺少 ffmpeg", "登录态失效盖过了缺工具"
    bar.set_auth_expired(False)
    config.FFMPEG_PATH = _real_ffmpeg
    bar.refresh_theme()
    assert bar.status_label.text() == "未启动", "工具回来了胶囊却没跟着改口"
    assert bar.status_label.parentWidget().toolTip() == "", "缺工具的提示留在胶囊上了"
finally:
    config.FFMPEG_PATH = _real_ffmpeg
    bar.refresh_theme()
assert f"v{config.APP_VERSION}" in [label.text() for label in bar.findChildren(QLabel)], \
    "标题栏那枚小字还是写死的「GUI」"
_qss = theme.theme_qss()
_caption = _qss.split("QPushButton#captionBtn, QPushButton#captionClose {")[1]
_action = _qss.split("QPushButton#iconBtn, QPushButton#iconClose {")[1]
assert "background: transparent" in _caption[:120], "标题栏的窗口控制没走扁平样式"
assert "border: 1px solid" in _action[:120], "设置页里的真动作按钮被顺手压平了"
_margins = bar.layout().contentsMargins()
assert _margins.left() == _margins.right() == 16, _margins   # 原来 18/12，右边看着比左边挤
# 上下留白：条子从 56 收到 48，胶囊（30 高）正好填满中段，不再悬在中间空一圈
assert bar.height() == 48 and _margins.top() == 9, (bar.height(), _margins.top())
assert bar.status_label.parentWidget().height() == 48 - 9 * 2, "胶囊高度跟条子对不上了"
# 三枚窗口控制共用一枚底框：散着放太像「什么都没有」
_group = bar.btn_min.parentWidget()
assert _group is bar.btn_close.parentWidget() and _group.objectName() == "captionGroup", \
    "窗口控制没合成一组"
assert "QFrame#captionGroup {" in _qss, "那组底框没样式，看着还是散的"
# 悬停填充上下得留一样的空：框被条子拉成 30 高时，26 高的按钮会顶到上沿、下面空一截
_gap_top = bar.btn_min.y()
_gap_bottom = _group.height() - bar.btn_min.y() - bar.btn_min.height()
assert _gap_top == _gap_bottom == 3, (_gap_top, _gap_bottom)   # 1px 边框 + 2px 内边距
# 关闭符号：标题栏 / 设置窗 / 确认框以前各写各的（✕、X、×），同一件事看着像三个控件
assert bar.btn_close.text() == theme.CLOSE_GLYPH
_settings_close = [btn.text() for btn in window.settings_window.findChildren(QPushButton)
                   if btn.objectName() == "iconClose"]
assert _settings_close == [theme.CLOSE_GLYPH], ("设置窗的关闭键没跟主界面同字形", _settings_close)
assert "font-size: 12px" in bar.brand.styleSheet() \
    and "QLabel#appTitle { font-size: 15px" in _qss, "品牌字与标题只差 1px，读起来像同字号"
print("10c 标题栏: 胶囊六档（缺 ffmpeg 压在最前的红、登录态失效单独一档且托盘只在上升沿弹） + 版本号 + 控制键成组且框内上下对称 + 三处关闭同一字形 + 条子收到 48")

# 11) 退出：录制中要确认，确认后请求停止并等封箱
asks = []
widgets.confirm = lambda *args, **kwargs: asks.append(args[1:]) or False
with state.active_downloads_lock:
    state.active_downloads.add(KEY)
window._force_quit = True
window._quit_confirmed = False
assert window._prepare_exit() is False, "用户选「不退」时不能退出"
assert KEY in state.active_downloads
assert asks and asks[0][0] == "还有录制在进行", "退出确认没走主题化的 confirm"
assert asks[0][-2:] == ("退出并保存", "继续录制"), "确认框按钮还是英文 Yes / No"
widgets.confirm = lambda *args, **kwargs: True


def _fake_stop(key):
    with state.active_downloads_lock:
        state.active_downloads.discard(key)
    return True


recorder.request_stop_download = _fake_stop
assert window._prepare_exit() is True
assert KEY not in state.active_downloads
with state.active_downloads_lock:
    state.active_downloads.discard(KEY)
print("11 退出: 先确认再停录，等待封箱")

# 11b) 应用图标：托盘与任务栏共用一枚，五档尺寸都要有，环和点在小尺寸上还分得开
app.setWindowIcon(app_icon.icon())        # 生产在 main.py 里设，这里走同一条路
assert not app.windowIcon().isNull(), "任务栏图标没设上：Alt-Tab 那栏还是 Qt 的默认图"
icon = app_icon.icon()
assert {s.width() for s in icon.availableSizes()} == set(app_icon.SIZES), icon.availableSizes()
big = icon.pixmap(48, 48).toImage()
assert big.pixelColor(0, 0).alpha() == 0, "四角没透明，任务栏上会拖出一块方底"
centre, ring, gap = big.pixelColor(24, 24), big.pixelColor(24, 12), big.pixelColor(24, 16)
assert centre.red() > 180 and centre.green() < 90, f"中心不是录制红：{centre.name()}"
assert ring.red() > 200 and ring.green() > 200 and ring.blue() > 200, f"白环没画出来：{ring.name()}"
assert gap.red() < 120, f"环与点之间没留缝，16px 上会糊成一团：{gap.name()}"
tile = big.pixelColor(6, 24)              # 卡片左沿：深灰底，不再是平台那套品牌红
assert tile.red() < 120 and tile.blue() > tile.red(), f"底又红回品牌色了：{tile.name()}"
small = icon.pixmap(16, 16).toImage()
dot16 = small.pixelColor(8, 8)
assert dot16.red() > 180 and dot16.green() < 90, f"16px 上红点糊掉了：{dot16.name()}"
print(f"11b 应用图标: 五档 {sorted(app_icon.SIZES)}，48px 中心 {centre.name()} / 环 {ring.name()}"
      f" / 底 {tile.name()}，16px 中心 {dot16.name()}")

# 12) 关闭主窗口不应该把在录任务丢掉
window.close()
app.processEvents()
print("12 关窗收尾通过")

print(f"\n[{utils.current_time_formatted()}] 界面层冒烟测试全部通过")
