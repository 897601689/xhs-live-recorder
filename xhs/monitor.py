"""轮询主循环：终端模式与 GUI 共用"""

import random
import threading
import time

from xhs import api, settings, state


def run_monitor_loop(stop_event=None, min_interval=None, max_interval=None) -> None:
    """轮询关注列表并启动下载。

    Args:
        stop_event: threading.Event，置位后在下一次等待点退出（GUI 停止按钮使用）
        min_interval: 轮询最小间隔（秒）
        max_interval: 轮询最大间隔（秒）
    """
    default_min, default_max = settings.current_poll_interval()
    min_interval = default_min if min_interval is None else int(min_interval)
    max_interval = default_max if max_interval is None else int(max_interval)
    if max_interval < min_interval:
        min_interval, max_interval = max_interval, min_interval

    state.emit_log(f"启动用户监控系统（轮询间隔 {min_interval}-{max_interval} 秒）")
    while True:
        if stop_event is not None and stop_event.is_set():
            break
        users = api.fetch_following_users()
        if users is None:
            state.emit_log("轮询失败：未能获取关注列表，稍后重试", level="warn")
        else:
            # 同步整份关注列表到界面（含未开播用户）
            state.notify_ui("following", {"users": users})
            # 只保留仍在直播的场次：下播的 room_id 自动出队，守卫集合不会无限增长
            state.prune_launch_guard({f"{u.get('user_id', '')}_{u.get('room_id', '')}"
                                for u in users if u.get("is_live")})
            live_users = [u for u in users
                          if u.get("is_live") and u.get("video_url") and u.get("auto_record")]
            # 同一场直播（同一 room_id）只自动开录一次，否则每轮轮询都会重新录一遍；
            # 录制异常中止时由 download_flv_stream_with_pipe 放行守卫，下一轮可以续上
            fresh_users = []
            for user_info in live_users:
                key = f"{user_info.get('user_id', '')}_{user_info.get('room_id', '')}"
                if state.is_launched(key):
                    continue
                state.mark_launched(key)
                fresh_users.append(user_info)
            if fresh_users:
                state.emit_log(f"发现 {len(fresh_users)} 个直播中用户，已开始自动录制")
                for user_info in fresh_users:
                    thread = threading.Thread(target=api.check_and_launch_download,
                                              args=(api.build_download_info(user_info),), daemon=True)
                    thread.start()
        wait_seconds = random.randint(min_interval, max_interval)
        if stop_event is not None:
            if stop_event.wait(wait_seconds):  # 可中断睡眠，无需等满整轮即可停止
                break
        else:
            time.sleep(wait_seconds)
