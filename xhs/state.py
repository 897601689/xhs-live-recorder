"""跨线程的运行状态：进度表、界面回调桥、录制任务登记、磁盘余量查询"""

import os
import shutil
import threading
from collections import OrderedDict

from xhs import config, utils


# ============================================================
# 进度管理器：单行动态刷新
# ============================================================
_download_progress = OrderedDict()  # 保持插入顺序
_download_progress_lock = threading.Lock()
_render_lock = threading.Lock()  # 渲染锁，确保原子操作

# ============================================================
# UI 桥接：GUI 注册监听器后，进度/日志以回调形式上抛
#          注意：监听器在工作线程中被调用，内部只应转发信号，禁止直接操作控件
# ============================================================
_ui_listener = None
_ui_listener_lock = threading.Lock()


def set_ui_listener(listener) -> None:
    """注册 UI 监听器，传 None 恢复为纯终端输出模式"""
    global _ui_listener
    with _ui_listener_lock:
        _ui_listener = listener


def get_ui_listener():
    with _ui_listener_lock:
        return _ui_listener


def notify_ui(event: str, payload: dict) -> None:
    """向 UI 抛出事件（progress / removed / finished / log / auth）

    异常一律忽略，任何界面问题都不应影响正在进行的录制任务。
    """
    listener = get_ui_listener()
    if listener is None:
        return
    try:
        listener(event, payload)
    except Exception:
        pass


def emit_log(message: str, level: str = "info") -> None:
    """统一日志出口：终端打印 + 磁盘追加 + 通知 GUI"""
    mark = "!" if level == "error" else ("*" if level == "warn" else " ")
    line = f"[{utils.current_time_formatted()}]{mark} {message}"
    try:
        # 打包成无控制台窗口（pythonw / pyinstaller --noconsole）时 sys.stdout 可能是 None
        print(line)
    except (OSError, ValueError):
        pass
    _append_log_file(line)
    notify_ui("log", {"message": message, "level": level})


_log_file_lock = threading.Lock()


def _append_log_file(line: str) -> None:
    """把同一行日志追加进 recorder.log，超过上限转成 .1（只留一份旧的）

    每行都开一次文件、写完就关：常驻进程里长开句柄意味着被强杀时缓冲区里的
    最后几十行一起没了，而那恰恰是最想知道的部分。日志量本来就稀（轮询几十秒
    一条、录制只在起止各报一次），这点开销换得到「崩了也留得下证据」。

    这里吞掉一切异常：写日志失败绝不能反过来影响正在录的流。
    """
    try:
        with _log_file_lock:
            os.makedirs(config.CONFIG_DIR, exist_ok=True)
            try:
                if os.path.getsize(config.LOG_FILE) > config.LOG_MAX_BYTES:
                    os.replace(config.LOG_FILE, config.LOG_FILE + ".1")
            except FileNotFoundError:
                pass
            with open(config.LOG_FILE, "a", encoding="utf-8") as fp:
                fp.write(line + "\n")
    except Exception:
        pass


def log_file_path() -> str:
    """日志文件路径（界面里「打开日志文件」要用，也让用户知道去哪儿找）"""
    return config.LOG_FILE


def _format_progress_line():
    """格式化进度为单行字符串（只保留图标）"""
    with _download_progress_lock:
        rows = list(_download_progress.items())

    if not rows:
        return ""

    parts = []
    for key, data in rows:
        nickname = data['nickname'][:12]
        size = data['size']
        speed = data['speed']
        elapsed = data['elapsed']

        # 格式化单个用户的进度
        part = f"\033[32m[{utils.current_time_formatted()}] [{nickname}] 📥 {size:>8} 🌠{speed:>10} 🕐 {elapsed:>5}\033[0m"  #

        parts.append(part)

    return " | ".join(parts)


def _render_progress():
    """渲染进度到单行"""
    with _render_lock:
        line = _format_progress_line()
        if line:
            print(f"\r{line}", end="", flush=True)


def _update_progress(download_key, nickname, size, speed, elapsed, user_id="", raw_bytes=0):
    """更新指定下载任务的进度"""
    if download_key is None:
        return
    with _download_progress_lock:
        _download_progress[download_key] = {
            "nickname": nickname,
            "size": size,
            "speed": speed,
            "elapsed": elapsed,
        }
    # 带上 user_id：界面不必再从 "user_id_room_id" 这种拼串里反解
    # 带上原始字节数：界面要按整场合计，格式化后的字符串没法再加回去
    notify_ui("progress", {
        "key": download_key,
        "user_id": user_id,
        "nickname": nickname,
        "size": size,
        "speed": speed,
        "elapsed": elapsed,
        "bytes": raw_bytes,
    })
    if get_ui_listener() is None:  # 已有 GUI 接管时不再刷新终端单行
        _render_progress()


def _remove_progress(download_key, user_id=""):
    """下载完成后移除进度行"""
    if download_key is None:
        return
    with _download_progress_lock:
        _download_progress.pop(download_key, None)
    notify_ui("removed", {"key": download_key, "user_id": user_id})
    if get_ui_listener() is None:
        _render_progress()


# ============================================================
# 录制任务状态（提前定义：check_and_launch_download 等下游函数都要用）
# ============================================================
active_downloads = set()          # 正在录制的 download_key（f"{user_id}_{room_id}"）
active_downloads_lock = threading.Lock()

stop_requested = set()            # 被要求停止录制的 download_key
stop_requested_lock = threading.Lock()

launch_guard = set()              # 本轮已自动开录的场次，同一场直播不重复开录
launch_guard_lock = threading.Lock()


def mark_launched(key: str) -> None:
    """登记一场直播已自动开录"""
    with launch_guard_lock:
        launch_guard.add(key)


def allow_relaunch(key: str) -> None:
    """放行某场直播重新开录：异常中断时使用，正常下播收尾则保留守卫"""
    with launch_guard_lock:
        launch_guard.discard(key)


def is_launched(key: str) -> bool:
    with launch_guard_lock:
        return key in launch_guard


def prune_launch_guard(live_keys: set) -> None:
    """只保留当前仍在直播的场次：下播的 room_id 自动出队，守卫集合不会无限增长"""
    with launch_guard_lock:
        launch_guard.intersection_update(live_keys)


def free_disk_bytes(path: str) -> int:
    """取所在磁盘剩余空间；取不到（目录未创建 / 网络盘）时返回一个足够大的值"""
    probe = path
    while probe and not os.path.isdir(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    try:
        return shutil.disk_usage(probe or ".").free
    except Exception:
        return config.DISK_MIN_FREE_START * 10
