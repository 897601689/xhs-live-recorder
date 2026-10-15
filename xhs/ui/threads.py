"""Qt 线程桥：把核心层的回调与耗时请求搬到主线程"""

import threading
from xhs import api
from xhs import monitor
from xhs import net
from PySide6.QtCore import QObject, QThread, Signal


class FollowingLoadThread(QThread):
    """一次性拉取整份关注列表（程序启动 / 手动刷新使用）"""

    loaded = Signal(list)
    failed = Signal(str)

    def run(self) -> None:
        try:
            users = api.fetch_following_users()
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        if users is None:
            self.failed.emit("网络请求失败或接口返回异常")
        else:
            self.loaded.emit(users)

class SidTestThread(QThread):
    """用当前 sid 打一次关注列表接口，验证登录态是否仍然有效"""

    finished_with = Signal(bool, str)      # (是否有效, 说明)

    def run(self) -> None:
        try:
            response = net._session().get(net.base_url, headers=net.build_headers(),
                                     params={"cursor": "", "order": "2"}, timeout=10)
        except Exception as exc:
            self.finished_with.emit(False, f"请求异常: {exc}")
            return
        if response.status_code != 200:
            self.finished_with.emit(False, f"HTTP {response.status_code}")
            return
        try:
            data = response.json()
        except Exception:
            self.finished_with.emit(False, "返回内容不是 JSON")
            return
        if data.get("success") is False:
            self.finished_with.emit(False, data.get("msg") or "登录已过期")
            return
        users = (data.get("data") or {}).get("user_list") or []
        self.finished_with.emit(True, f"有效，可读到 {len(users)} 位关注用户")

class UiBridge(QObject):
    """跨线程信号桥：工作线程 -> GUI 线程

    每个事件都带上 user_id：key 形如 user_id_room_id，靠 split("_", 1) 反解
    在 user_id 里含下划线时就会截错，而且每来一次进度都要重解一遍。
    """

    task_progress = Signal(str, str, str, str, str, str, int)   # key, user_id, nickname, size, speed, elapsed, bytes
    task_removed = Signal(str, str)                        # key, user_id
    task_finished = Signal(str, str, str, str, str, str)   # key, user_id, nickname, size, duration, avg_speed
    task_started = Signal(str, str, str)                   # key, user_id, nickname
    following_updated = Signal(list, bool)                 # users, verbose
    logged = Signal(str, str)                              # message, level
    auth_state = Signal(bool, str)                         # 登录态是否失效, 说明


class MonitorThread(QThread):
    """轮询线程：内部调用与主命令相同的 run_monitor_loop"""

    def __init__(self, min_interval: int, max_interval: int, parent=None):
        super().__init__(parent)
        self._min_interval = min_interval
        self._max_interval = max_interval
        self._stop_event = threading.Event()

    def request_stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        monitor.run_monitor_loop(stop_event=self._stop_event,
                         min_interval=self._min_interval,
                         max_interval=self._max_interval)
