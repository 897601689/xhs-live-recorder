"""程序入口：解析启动参数，决定开图形界面还是跑纯终端模式"""

import io
import sys

from xhs import config, instance, monitor, profiles, settings, state, utils

# 强制 UTF-8 输出（GUI / pythonw 下 stdout 可能为 None，需跳过）
if sys.platform == 'win32':
    for _stream_name in ('stdout', 'stderr'):
        _stream = getattr(sys, _stream_name, None)
        if _stream is not None and hasattr(_stream, 'buffer'):
            try:
                setattr(sys, _stream_name,
                        io.TextIOWrapper(_stream.buffer, encoding='utf-8', errors='replace'))
            except Exception:
                pass
    del _stream_name, _stream

# GUI 依赖（可选）：没装 PySide6 时 --cli 仍可用；界面模块只在真要开界面时才 import，
# 免得终端模式也被 Qt 依赖卡住
_HAS_PYSIDE6 = True
try:
    import PySide6      # noqa: F401
except Exception:
    _HAS_PYSIDE6 = False


def _log_startup(mode: str) -> None:
    """开跑前把环境事实写进日志：事后查问题时最先要问的就是这几项

    只记「登录态有没有配」，绝不记它的值 —— 这个文件会留在磁盘上。
    """
    state.emit_log(f"启动 {config.PLATFORM_NAME}直播录制器 v{config.APP_VERSION}"
                   f"（{mode}）| Python {sys.version.split()[0]}"
                   f" | ffmpeg: {config.FFMPEG_PATH or '未找到'}"
                   # 预览只依赖 ffplay：它没找到时菜单里那项是灰的，日志得说清是不是这个原因
                   f" | ffplay: {config.FFPLAY_PATH or '未找到（实时预览不可用）'}"
                   f" | 输出: {settings.output_root()}"
                   f" | 配置: {config.CONFIG_DIR}"
                   f" | 登录态: {'已配置' if settings.current_sid() else '未配置'}")


def run_cli() -> None:
    """终端模式（原有行为）：python main.py --cli"""
    if not instance.acquire():
        # 打包版没控制台，stdout 是 None，print 等于对着空气喊话；emit_log 会同时把这行
        # 落到 recorder.log 里 —— 用户说「我双击了两次但没见第二个窗」时，磁盘上有记录
        state.emit_log("已经有一个实例在跑了，本进程不再启动（两个进程会各录一份同一场直播）",
                       "warn")
        return
    _log_startup("终端")
    try:
        monitor.run_monitor_loop()
    except KeyboardInterrupt:
        print(f"\n[{utils.current_time_formatted()}] 用户终止程序...")
        print("程序已退出")
    finally:
        profiles.flush_user_profiles()   # 档案是延迟落盘的，退出前必须冲一次，否则这轮改动会丢


def launch_gui() -> None:
    """启动图形界面"""
    if not _HAS_PYSIDE6:
        print(f"[{utils.current_time_formatted()}] 未检测到 PySide6，无法启动界面。")
        print(f"[{utils.current_time_formatted()}] 安装命令: pip install PySide6")
        print(f"[{utils.current_time_formatted()}] 或先使用终端模式: python main.py --cli")
        return
    from PySide6.QtWidgets import QApplication, QMessageBox
    from xhs.ui import app_icon, main_window, theme
    app = QApplication(sys.argv)
    # 抢锁放在套样式之前：全局半透明规则会把原生消息框刷成黑底黑字，这时候还没套
    if not instance.acquire():
        QMessageBox.information(None, "已经在运行",
                                "直播录制器已经开着了——系统托盘里那枚深灰图标就是它。\n"
                                "再开一个进程会各录一份同样的直播，所以这里不再启动。")
        return
    _log_startup("界面")
    app.setFont(theme.default_font())
    app.setWindowIcon(app_icon.icon())      # 任务栏 / Alt-Tab：不设就是 Qt 的默认图标
    theme.install_app_style(app)          # 鼠标点击不留焦点虚线框，键盘 Tab 聚焦才画
    theme.install_rounded_tooltips(app)   # 提示窗改成透明底，样式表里的圆角才不会被四个方角拖累
    theme.disable_combo_roll(app)         # 下拉弹层的展开动画会把没画的像素上屏成黑条
    app.setStyleSheet(theme.theme_qss())   # 全局生效：弹窗提示、菜单也一起套用当前主题
    app.setQuitOnLastWindowClosed(False)   # 主窗最小化到托盘后不退出进程
    app.aboutToQuit.connect(profiles.flush_user_profiles)   # 兜底：任何退出路径都把档案冲下去
    window = main_window.MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    if "--cli" in sys.argv[1:]:
        run_cli()
    else:
        launch_gui()
