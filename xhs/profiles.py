"""用户档案（昵称 / 自动录制开关 / 最近录制时间）读写与防抖落盘"""

import json
import os
import threading
from datetime import datetime

from xhs import config, state


# 用户档案（以 user_id 为主键，昵称会改、user_id 不会）：
# user_id -> {"name": 昵称, "auto_record": 是否参与自动录制, "time": 最近录制时间}
_user_profiles = {}
_profiles_lock = threading.Lock()

AUTO_RECORD_KEY = "auto_record"   # 自动录制开关键


def _migrate_legacy_files() -> None:
    """旧档案改名到位、旧开关文件退役，一次性收尾"""
    try:
        if not os.path.exists(config.USER_PROFILES_FILE) and os.path.exists(config.LEGACY_LAST_RECORD_FILE):
            os.replace(config.LEGACY_LAST_RECORD_FILE, config.USER_PROFILES_FILE)
    except Exception as exc:
        state.emit_log(f"旧版档案改名失败: {exc}", level="warn")
    try:
        if os.path.exists(config.LEGACY_AUTO_RECORD_FILE):
            os.remove(config.LEGACY_AUTO_RECORD_FILE)
    except Exception as exc:
        state.emit_log(f"旧版开关文件清理失败: {exc}", level="warn")


def _write_user_profiles(snapshot: dict) -> None:
    """把用户档案写入磁盘（先写临时文件再替换，避免中途退出写坏）"""
    try:
        os.makedirs(config.CONFIG_DIR, exist_ok=True)
        temp_path = config.USER_PROFILES_FILE + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as fp:
            json.dump({"version": 1, "records": snapshot}, fp, ensure_ascii=False, indent=2)
        os.replace(temp_path, config.USER_PROFILES_FILE)
    except Exception as exc:
        state.emit_log(f"用户档案保存失败: {exc}", level="warn")


def _save_user_profiles() -> None:
    """持久化当前全部用户档案（昵称 / 自动录制开关 / 最近录制时间）"""
    with _profiles_lock:
        snapshot = {
            str(uid): {
                "name": info.get("name") or "",
                AUTO_RECORD_KEY: bool(info.get(AUTO_RECORD_KEY, True)),
                "time": info.get("time") or "",
            }
            for uid, info in _user_profiles.items()
        }
    _write_user_profiles(snapshot)


_profiles_flush_timer = None
_profiles_flush_lock = threading.Lock()


def _schedule_profiles_save() -> None:
    """合并一段时间内的档案改动再落盘

    轮询每轮都会逐用户同步昵称，几百位用户里只要有人改名就是整表写盘；
    这种改动不在关键路径上，晚几秒落盘没有任何影响，也不必每改一条就全量写一次。
    """
    global _profiles_flush_timer
    with _profiles_flush_lock:
        if _profiles_flush_timer is not None:
            return
        timer = threading.Timer(config.PROFILES_FLUSH_DELAY, _flush_profiles)
        timer.daemon = True
        _profiles_flush_timer = timer
        timer.start()


def _flush_profiles() -> None:
    global _profiles_flush_timer
    with _profiles_flush_lock:
        timer, _profiles_flush_timer = _profiles_flush_timer, None
    if timer is not None:
        try:
            timer.cancel()
        except Exception:
            pass
    _save_user_profiles()


def flush_user_profiles() -> None:
    """退出前把延迟落盘的档案立即写下去"""
    _flush_profiles()


def load_user_profiles() -> None:
    """启动时读入用户档案：最近录制时间与自动录制开关都以该文件为准"""
    _migrate_legacy_files()
    try:
        with open(config.USER_PROFILES_FILE, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        records = data.get("records") if isinstance(data, dict) else None
        if not isinstance(records, dict):
            return
        with _profiles_lock:
            for uid, info in records.items():
                if not isinstance(info, dict):
                    continue
                _user_profiles[str(uid)] = {
                    "name": str(info.get("name") or ""),
                    AUTO_RECORD_KEY: bool(info.get(AUTO_RECORD_KEY, True)),
                    "time": str(info.get("time") or ""),
                }
    except FileNotFoundError:
        pass
    except Exception as exc:
        state.emit_log(f"用户档案读取失败: {exc}", level="warn")


def _touch_entry(user_id: str, nickname: str = "") -> dict:
    """确保该用户有一条档案，昵称变化即时纠正（须在锁内调用）"""
    entry = _user_profiles.get(user_id)
    if entry is None:
        entry = {"name": nickname or "", AUTO_RECORD_KEY: True, "time": ""}
        _user_profiles[user_id] = entry
    elif nickname and entry.get("name") != nickname:
        entry["name"] = nickname
    return entry


def sync_user_profile(user_id: str, nickname: str = "") -> str:
    """拉取关注列表时登记该用户（同步最新昵称），返回其最近录制时间"""
    if not user_id:
        return ""
    with _profiles_lock:
        before = dict(_user_profiles.get(user_id) or {})
        entry = dict(_touch_entry(user_id, nickname))
    if before != entry:
        _schedule_profiles_save()
    return entry.get("time") or ""


def mark_last_record(user_id: str, nickname: str = "", text: str = None) -> str:
    """写入一次最近录制时间并立即落盘，返回写入的时间"""
    text = text or datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    if not user_id:
        return text
    with _profiles_lock:
        _touch_entry(user_id, nickname)["time"] = text
    _save_user_profiles()
    return text


def is_auto_record(user_id: str) -> bool:
    """该用户是否参与自动录制：轮询发现开播时自动开录，默认开启"""
    with _profiles_lock:
        entry = _user_profiles.get(user_id)
    if entry is not None:
        return bool(entry.get(AUTO_RECORD_KEY, True))
    return True


def set_auto_record(user_id: str, nickname: str, enabled: bool) -> bool:
    """切换自动录制开关并写入用户档案"""
    if user_id:
        with _profiles_lock:
            _touch_entry(user_id, nickname)[AUTO_RECORD_KEY] = bool(enabled)
        _save_user_profiles()
    return bool(enabled)


# 启动时读入用户档案（昵称 / 自动录制开关 / 最近录制时间）
load_user_profiles()
