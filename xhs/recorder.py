"""ffmpeg 管道录制：断流续录、有界重试、分段落盘"""

import os
import requests
import subprocess
import time
from datetime import timedelta

from xhs import config, net, settings, state, utils


# 单次录制的收尾状态
REC_DONE = "done"        # 流自然结束（下播）
REC_STOPPED = "stopped"  # 用户手动停止
REC_RETRY = "retry"      # 可恢复的中断（网络抖动 / ffmpeg 崩了），重连续录
REC_FATAL = "fatal"      # 不再重试（磁盘不足 / 目录建不出来）


def _delete_output(path: str, nickname: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        state.emit_log(f"[{nickname}] 清理残留文件失败: {exc}", level="error")


def _keep_partial(output_filename: str, nickname: str, reason: str) -> int:
    """中断收尾：够长就保留（能播到中断点），只剩空头才删掉"""
    try:
        size = os.path.getsize(output_filename)
    except OSError:
        return 0
    if size < config.PARTIAL_MIN_BYTES:
        state.emit_log(f"[{nickname}] {reason}，已写入 {utils.format_file_size(size)}（不足 {utils.format_file_size(config.PARTIAL_MIN_BYTES)}），删除残留文件",
                 level="warn")
        _delete_output(output_filename, nickname)
        return size
    state.emit_log(f"[{nickname}] {reason}，文件已保留（{utils.format_file_size(size)}，可播放到中断点）: "
             f"{os.path.basename(output_filename)}", level="warn")
    return size


def _wait_retry(seconds: int, download_key) -> bool:
    """可打断的退避等待；期间被要求停止则返回 True"""
    for _ in range(seconds * 10):
        if _is_stop_requested(download_key):
            return True
        time.sleep(0.1)
    return _is_stop_requested(download_key)


def download_flv_stream_with_pipe(user_info, download_key=None):
    """录制一场直播：断流后退避重连、分段续录，直到下播 / 手动停止 / 重试用尽

    Args:
        user_info (dict): 包含用户信息和直播链接的字典
        download_key (str): 下载任务的唯一标识符
    """
    nickname = user_info.get('nickname', '')
    user_id = str(user_info.get('user_id', ''))
    # 命名规则只渲染一次：分段续录要落在同一个目录、同一组文件名上
    rel_dir, file_base = settings.render_name_template(settings.current_name_template(), user_info)
    root_dir = settings.output_root()
    output_dir = os.path.join(root_dir, rel_dir) if rel_dir else root_dir

    try:
        os.makedirs(output_dir, exist_ok=True)
    except OSError as exc:
        state.emit_log(f"[{nickname}] 无法创建保存目录 {output_dir}: {exc}", level="error")
        state.allow_relaunch(download_key)
        return

    session_start = time.time()
    total_size = 0
    segment = 0        # 已写出的段数（第 1 段用主文件名，之后加 -2 / -3）
    restarts = 0
    give_up = ""

    while True:
        output_filename = (os.path.join(output_dir, f"{file_base}.mkv") if segment == 0
                           else os.path.join(output_dir, f"{file_base}-{segment + 1}.mkv"))
        free = state.free_disk_bytes(output_dir)
        if free < config.DISK_MIN_FREE_START:
            give_up = f"磁盘剩余仅 {utils.format_file_size(free)}，不足以开始录制"
            state.emit_log(f"[{nickname}] {give_up}", level="error")
            break

        status, seg_size = _record_once(user_info, download_key, output_filename,
                                       session_start, segment)
        segment += 1
        total_size += seg_size

        if status in (REC_DONE, REC_STOPPED):
            break
        if status == REC_FATAL:
            give_up = "录制不可恢复地中止"
            break
        if _is_stop_requested(download_key):
            break
        restarts += 1
        if restarts > config.RECORD_MAX_RESTARTS:
            give_up = f"连续重连 {config.RECORD_MAX_RESTARTS} 次仍未恢复"
            state.emit_log(f"[{nickname}] {give_up}，本场录制到此为止", level="error")
            break
        delay = config.RECORD_RESTART_WAITS[min(restarts - 1, len(config.RECORD_RESTART_WAITS) - 1)]
        state.emit_log(f"[{nickname}] 直播流中断，{delay} 秒后续录第 {segment + 1} 段", level="warn")
        if _wait_retry(delay, download_key):
            break

    # 任务真正结束才摘掉登记：分段重连的间隙里不能让别人把同一场直播再开一遍
    if download_key:
        with state.active_downloads_lock:
            state.active_downloads.discard(download_key)
        state._remove_progress(download_key, user_id)

    manually_stopped = _consume_stop_request(download_key)

    if manually_stopped:
        state.emit_log(f"[{nickname}] 已手动停止录制，共 {utils.format_file_size(total_size)}"
                 f"（{segment} 段），文件已保存", level="warn")
    elif give_up:
        # 没录成：放行守卫，下一轮轮询发现还在播就再试一次
        state.allow_relaunch(download_key)
        state.emit_log(f"[{nickname}] 录制中止：{give_up}", level="error")
    else:
        _report_session_done(user_info, download_key, output_dir, file_base,
                             total_size, segment, session_start)


def _report_session_done(user_info, download_key, output_dir, file_base, total_size,
                         segments, session_start) -> None:
    """直播自然结束：汇总本场录制结果并上抛 finished 事件"""
    nickname = user_info.get('nickname', '')
    total_time = max(time.time() - session_start, 0.001)
    # 落盘体积按整场目录里的本次产物估（分段录制时每段各自成文件）
    final_size = total_size
    try:
        final_size = sum(os.path.getsize(os.path.join(root, name))
                         for root, _dirs, files in os.walk(output_dir)
                         for name in files
                         if name.startswith(file_base) and name.endswith(".mkv")) or total_size
    except OSError:
        pass
    final_size_str = utils.format_file_size(final_size)
    avg_speed = final_size / total_time / (1024 * 1024)
    time_formatted = utils.transform_int_to_time(int(total_time * 1000)).split(" ")[-1]

    state.notify_ui("finished", {
        "key": download_key,
        "user_id": str(user_info.get("user_id", "")),
        "nickname": nickname,
        "size": final_size_str,
        "path": os.path.join(output_dir, f"{file_base}.mkv"),
        "duration": time_formatted,
        "avg_speed": f"{avg_speed:.2f} MB/s",
        "segments": segments,
    })
    tail = f"（共 {segments} 段，断流后续录过）" if segments > 1 else ""
    state.emit_log(f"[{nickname}] 下载完成! 文件大小: {final_size_str} | "
             f"总用时: {time_formatted} | 平均速度: {avg_speed:.2f} MB/s {tail}")


def _record_once(user_info, download_key, output_filename, session_start, segment):
    """
    使用管道将FLV直播流直接传输给ffmpeg，边下载边修复时间戳
    避免先下载完整FLV再转换，节省磁盘空间和I/O

    Returns:
        (str, int): 收尾状态（REC_*）与本次写出的字节数
    """
    url = user_info['video_url']
    nickname = user_info.get('nickname', '')
    user_id = str(user_info.get('user_id', ''))

    start_time = time.time()
    last_time = start_time
    last_size = 0
    total_size = 0

    # 启动ffmpeg进程，从stdin接收数据
    ffmpeg_command = [
        config.FFMPEG_PATH,
        '-hide_banner',  # 隐藏ffmpeg版本信息
        '-loglevel', 'error',  # 只显示错误信息
        '-i', 'pipe:0',  # 从stdin读取输入
        '-c', 'copy',  # 直接复制流，不重新编码
        '-f', 'matroska',  # 强制输出格式为mkv
        output_filename
    ]

    ffmpeg_process = None
    response = None
    output_dir = os.path.dirname(output_filename)

    if config.FFMPEG_PATH is None:
        state.emit_log(f"[{nickname}] 未找到 ffmpeg，无法录制", level="error")
        return REC_FATAL, 0

    try:
        # 启动ffmpeg子进程
        ffmpeg_process = subprocess.Popen(
            ffmpeg_command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,   # 避免 ffmpeg 输出写满管道缓冲区导致死锁
            stderr=subprocess.DEVNULL,
            creationflags=config.NO_CONSOLE,
        )

        # 开始下载流：连接 10 秒、两次读之间超过 30 秒没数据即判定断流（交给上层重连）
        response = net._session().get(url, stream=True, timeout=(10, 30))
        response.raise_for_status()

        if segment == 0:
            # 自动录制往往发生在用户没看着界面的时候：流真的连上了才提示，避免误报
            state.notify_ui("started", {"key": download_key,
                                  "user_id": user_id,
                                  "nickname": nickname})

        # 将下载的数据实时写入ffmpeg的stdin
        # 64KB 一块：直播码率几 MB/s 时，8KB 会让 Python 层的循环开销放大近十倍
        ffmpeg_dead = False
        disk_low = False
        manually_stopped = False
        for chunk in response.iter_content(chunk_size=65536):
            if _is_stop_requested(download_key):  # 用户点击「停止录制」
                manually_stopped = True
                break
            # ffmpeg 崩了不会有任何通知，继续写 stdin 只会抛 BrokenPipe 被通用 except
            # 吞掉，日志里只剩一句没有上下文的「下载出错」；每块之前查一次存活
            if ffmpeg_process.poll() is not None:
                ffmpeg_dead = True
                break
            if not chunk:
                continue
            try:
                ffmpeg_process.stdin.write(chunk)
            except (BrokenPipeError, OSError) as exc:
                ffmpeg_dead = True
                state.emit_log(f"[{nickname}] 写入 ffmpeg 失败: {exc}", level="error")
                break
            total_size += len(chunk)

            # 每隔1秒更新一次进度信息
            current_time = time.time()
            if current_time - last_time >= 1.0:
                # 计算下载速度
                elapsed_time = current_time - last_time
                speed = (total_size - last_size) / elapsed_time  # bytes/sec
                speed_mb = speed / (1024 * 1024)  # MB/s

                # 计算已用时间（整场计时，分段续录时不会归零）
                time_elapsed = current_time - session_start
                size_str = utils.format_file_size(total_size)
                state._update_progress(download_key, nickname, size_str, f"{speed_mb:.2f} MB/s",
                                 f"{_format_hms(time_elapsed)}({time_elapsed:.1f}s)", user_id,
                                 total_size)

                # 更新上次记录
                last_time = current_time
                last_size = total_size

                # 磁盘写穿是录制器最现实的失败：留出收尾余量，主动正常封箱
                if state.free_disk_bytes(output_dir) < config.DISK_MIN_FREE_KEEP:
                    disk_low = True
                    break

        # 收尾：关 stdin 让 ffmpeg 按 EOF 正常封箱（mkv 缺 index 就不可播）
        try:
            ffmpeg_process.stdin.close()
        except OSError:
            pass
        try:
            ffmpeg_process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            state.emit_log(f"[{nickname}] ffmpeg 封箱超时，强制结束", level="error")
            ffmpeg_process.kill()
            ffmpeg_process.wait()

        if manually_stopped:
            state.emit_log(f"[{nickname}] 已手动停止本段录制，文件已保存: "
                     f"{os.path.basename(output_filename)}（{utils.format_file_size(total_size)}）",
                     level="warn")
            return REC_STOPPED, total_size   # 停止标记由上层统一取出，据此判定整场结局

        if ffmpeg_dead:
            state.emit_log(f"[{nickname}] ffmpeg 进程退出（返回码: {ffmpeg_process.returncode}），本段中断",
                     level="error")
            return REC_RETRY, _keep_partial(output_filename, nickname, "ffmpeg 中断")

        if disk_low:
            state.emit_log(f"[{nickname}] 磁盘剩余仅 {utils.format_file_size(state.free_disk_bytes(output_dir))}，"
                     f"停止录制并已正常封箱", level="error")
            return REC_FATAL, total_size

        # 能走到这里说明流是按 EOF 自然结束的（下播），返回码非 0 才算真失败
        if ffmpeg_process.returncode != 0:
            state.emit_log(f"[{nickname}] ffmpeg 收尾异常，退出码: {ffmpeg_process.returncode}",
                     level="error")
            return REC_RETRY, _keep_partial(output_filename, nickname, "本段收尾异常")

        return REC_DONE, total_size

    except requests.exceptions.RequestException as e:
        state.emit_log(f"[{nickname}] 网络请求错误: {str(e)}", level="error")
        # 直播流的中断文件可播到中断点：有内容就保留，只剩空头才删
        return REC_RETRY, _keep_partial(output_filename, nickname, "网络中断")
    except Exception as e:
        state.emit_log(f"[{nickname}] 下载出错: {str(e)}", level="error")
        return REC_RETRY, _keep_partial(output_filename, nickname, "录制异常")
    finally:
        # 确保ffmpeg进程被终止
        if ffmpeg_process is not None:
            try:
                if ffmpeg_process.poll() is None:  # 进程仍在运行
                    ffmpeg_process.terminate()
                    try:
                        ffmpeg_process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        ffmpeg_process.kill()
                        ffmpeg_process.wait()
            except Exception as exc:
                state.emit_log(f"[{nickname}] 结束 ffmpeg 进程失败: {exc}", level="warn")

        # 确保响应被关闭
        if response is not None:
            try:
                response.close()
            except Exception as exc:
                state.emit_log(f"[{nickname}] 关闭流响应失败: {exc}", level="warn")


def _format_hms(seconds: float) -> str:
    duration = timedelta(seconds=seconds)
    hours, remainder = divmod(duration.total_seconds(), 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{int(hours):02d}:{int(minutes):02d}:{int(secs):02d}"


def _is_stop_requested(download_key) -> bool:
    """查询某个录制任务是否被要求停止（不清除标记）"""
    if not download_key:
        return False
    with state.stop_requested_lock:
        return download_key in state.stop_requested


def _consume_stop_request(download_key) -> bool:
    """取出并清除停止标记，返回该任务是否被手动停止"""
    if not download_key:
        return False
    with state.stop_requested_lock:
        if download_key in state.stop_requested:
            state.stop_requested.discard(download_key)
            return True
    return False


def request_stop_download(download_key: str) -> bool:
    """请求停止指定录制任务；返回 False 表示当前没有该任务在录制"""
    with state.active_downloads_lock:
        if download_key not in state.active_downloads:
            return False
    with state.stop_requested_lock:
        state.stop_requested.add(download_key)
    return True
