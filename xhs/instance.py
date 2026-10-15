"""单实例锁：一台机器上只允许一个录制进程

双击两次 exe（或者开机自启 + 又手动开一次）会起两条轮询、给同一场直播各录一份文件：
文件名带开始时间，所以两份不会互相覆盖，只会安静地占双倍磁盘、打双倍接口 —— 发现时
通常已经几个小时过去了。

锁用操作系统层面的文件区域锁而不是「写个 pid 文件看它在不在」：进程被强杀、被系统
重启干掉，锁都随句柄一起回收，不会留下一个骗人的死锁文件。
"""

import os
import sys

from xhs import config

LOCK_NAME = "app.lock"

_lock_handle = None      # 句柄必须一直开着：一关就等于放锁，所以不能交给局部变量


def acquire() -> bool:
    """抢到锁返回 True，已有实例在跑返回 False

    拿不到锁文件时（只读目录、磁盘满）返回 True 放行：宁可多开一个实例，也不要把
    一个环境问题变成「程序根本打不开」。
    """
    global _lock_handle
    path = os.path.join(config.CONFIG_DIR, LOCK_NAME)
    try:
        os.makedirs(config.CONFIG_DIR, exist_ok=True)
        handle = open(path, "a+", encoding="utf-8")
    except OSError:
        return True
    handle.seek(0)
    if not _try_lock(handle):
        handle.close()
        return False
    _lock_handle = handle
    try:                     # 记下是谁占着：排查时「哪个进程在录」比「有个锁」有用
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
    except OSError:
        pass                 # 记不上 pid 不影响锁本身
    return True


def _try_lock(handle) -> bool:
    if sys.platform == "win32":
        import msvcrt
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False
