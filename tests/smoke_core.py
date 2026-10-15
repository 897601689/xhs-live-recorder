"""核心层冒烟测试：断流续录 / 中断文件保护 / 启动守卫 / 档案落盘防抖 / 日志落盘轮转 / 单实例锁 / ffmpeg 子进程不带控制台 / 实时预览子进程

补丁都打在“名字所属的模块”上（跨模块调用已带模块前缀，同模块调用查模块全局表），
所以 recorder 内部对 _record_once / free_disk_bytes 的调用都能被下面替换掉。
"""
import json
import os
import pathlib
import sys
import tempfile
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import main as m          # noqa: F401  入口能 import 通就算过一道
from xhs import api, config, net, preview, profiles, recorder, settings, state, utils   # noqa: E402

config.RECORD_RESTART_WAITS = (0, 0, 0)
config.RECORD_MAX_RESTARTS = 3
# 录制一律写进临时目录，绝不碰用户真实的下载目录
TMP_ROOT = tempfile.mkdtemp(prefix="xhs_test_")
settings._ui_settings["output_dir"] = TMP_ROOT
events = []
state.notify_ui = lambda event, payload: events.append((event, payload))
logs = []
_orig_emit = state.emit_log
state.emit_log = lambda msg, level="info": logs.append((level, msg))
# 下面几项会把 _record_once 换成假实现，item 14 要真走一遍 Popen 调用点，先留个原件
_real_record_once = recorder._record_once

USER = {"nickname": "测试主播", "user_id": "u1", "room_id": "r1",
        "video_url": "http://example/flv", "room_start_time": "", "remark": "",
        "title": "", "desc": ""}
KEY = "u1_r1"


def run_case(statuses, stop_flag=False):
    """按给定状态序列驱动一次整场录制，返回每次调用的输出文件名"""
    logs.clear()
    events.clear()
    state.stop_requested.clear()
    if stop_flag:
        state.stop_requested.add(KEY)
    seen = []
    paths = []

    def fake_once(user_info, dk, path, ss, seg):
        seen.append(os.path.basename(path))
        paths.append(path)
        return statuses[len(seen) - 1], 2048

    recorder._record_once = fake_once
    recorder.download_flv_stream_with_pipe(USER, KEY)
    assert all(p.startswith(TMP_ROOT) for p in paths), paths   # 绝不写进真实下载目录
    return seen


def finished_events():
    return [e for e in events if e[0] == "finished"]


# 1) 断流两次后续录，最后自然下播 → 三个分段 + 一次 finished
files = run_case([recorder.REC_RETRY, recorder.REC_RETRY, recorder.REC_DONE])
assert len(files) == 3, files
assert files[0].startswith("测试主播") and files[1].endswith("-2.mkv"), files
done = finished_events()
assert len(done) == 1, events
assert done[0][1]["user_id"] == "u1" and done[0][1]["segments"] == 3, done[0][1]
assert KEY not in state.active_downloads, "整场结束要摘掉任务登记"
print("1 断流续录:", files)

# 2) 连续失败到重试用尽 → 不放 finished，守卫放行，error 日志
state.mark_launched(KEY)
run_case([recorder.REC_RETRY] * 4)
assert finished_events() == [], events
assert not state.is_launched(KEY), "重试用尽后应放行，下一轮轮询可续录"
assert any(l[0] == "error" and "连续重连" in l[1] for l in logs), logs
print("2 重试用尽:", [l[1] for l in logs if l[0] == "error"][-1])

# 3) 手动停止 → 守卫保留、不上抛 finished，只记一条停止日志
state.mark_launched(KEY)
run_case([recorder.REC_STOPPED], stop_flag=True)
assert finished_events() == [], events
assert any("已手动停止" in l[1] for l in logs), logs
assert state.is_launched(KEY), "手动停止不该被重新自动开录"
print("3 手动停止:", [l[1] for l in logs if "手动停止" in l[1]][0])

# 4) 磁盘不足：开录前就拒绝
state.mark_launched(KEY)
calls = []
recorder._record_once = lambda *a, **k: (calls.append(a), (recorder.REC_DONE, 0))[1]
real_free = state.free_disk_bytes
state.free_disk_bytes = lambda path: 10 * 1024 * 1024
logs.clear()
recorder.download_flv_stream_with_pipe(USER, KEY)
state.free_disk_bytes = real_free
assert calls == [], "磁盘不足时不应开始录制"
assert not state.is_launched(KEY)
print("4 磁盘不足:", [l[1] for l in logs if l[0] == "error"][-1])

# 5) 中断文件保护：够长保留、太短删除
tmp = os.path.join(TMP_ROOT, "partial.mkv")
with open(tmp, "wb") as fp:
    fp.write(b"0" * (config.PARTIAL_MIN_BYTES + 10))
assert recorder._keep_partial(tmp, "主播", "网络中断") > 0 and os.path.exists(tmp)
with open(tmp, "wb") as fp:
    fp.write(b"0" * 10)
recorder._keep_partial(tmp, "主播", "网络中断")
assert not os.path.exists(tmp), "空头文件应删除"
print("5 中断文件: 大文件保留 / 空头删除")

# 6) 启动守卫按在播集合修剪
state.mark_launched(KEY)
state.mark_launched("u9_r9")
state.prune_launch_guard({KEY})
assert state.is_launched(KEY) and not state.is_launched("u9_r9")
print("6 守卫修剪: 只保留在播场次")

# 7) 档案落盘防抖（轮询同步昵称不写盘，flush 才写）
writes = []
real_write = profiles._write_user_profiles
profiles._write_user_profiles = lambda snapshot: writes.append(len(snapshot))
profiles.sync_user_profile("u1", "新昵称")
assert writes == [], "轮询路径应合并落盘，不逐条写"
profiles.flush_user_profiles()
assert len(writes) == 1, writes
profiles._write_user_profiles = real_write
print("7 档案防抖: 合并后一次落盘")

# 8) flv 解析失败要说清楚原因
try:
    api.extract_flv_urls("https://example/live")
    raise AssertionError("应抛 ValueError")
except ValueError as exc:
    message = str(exc)
assert "query" in message
print("8 flv 解析:", message)

# 9) 未配置 sid 时不再静默：fetch 直接返回 None，并给界面递一枚红灯事件
saved = settings._ui_settings.get("sid")
settings._ui_settings["sid"] = ""
os.environ.pop(config.SID_ENV_VAR, None)
logs.clear()
events.clear()
try:
    assert api.fetch_following_users() is None
    assert any("未配置登录态" in l[1] for l in logs), logs
    # 级别必须是 warn：界面给每条 error 都弹一次托盘，轮询 30 秒一轮就是每半分钟弹一次
    assert [l[0] for l in logs if "未配置登录态" in l[1]] == ["warn"], logs
    assert ("auth", {"expired": True, "message": "还没有配置登录态 sid"}) in events, events
finally:
    settings._ui_settings["sid"] = saved
print("9 缺 sid:", logs[0][1])


# 9b) 主循环整条走一遍：请求已经抽成 net.fetch_page，喂一串假页面就能覆盖
#     翻页 / 跨页去重 / 登录态两分支 / 非 200 / 开播字段与档案副作用
_real_fetch_page = net.fetch_page
_real_write_profiles = profiles._write_user_profiles
written = []
profiles._write_user_profiles = lambda snapshot: written.append(len(snapshot))
settings._ui_settings["sid"] = "fake-sid-for-test-only"


def _feed(pages):
    """按顺序吐出假页面，并记下每页收到的 cursor（翻页参数传错要能看出来）"""
    cursors = []
    queue = list(pages)

    def _fake(cursor=""):
        cursors.append(cursor)
        return queue.pop(0)

    net.fetch_page = _fake
    return cursors


def _ok_page(users, cursor="", has_more=False):
    return {"success": True, "data": {"user_list": users, "cursor": cursor, "has_more": has_more}}


def _live_user(user_id, nickname, title="深夜聊天"):
    """造一位正在直播的关注用户：形状照抄真接口，标题埋在 room_extra_info 那层 JSON 字符串里"""
    return {"user_id": user_id, "nickname": nickname, "remark": "", "desc": "个人简介\n第二行",
            "fan_count": 12, "image": "http://x/i.jpg",
            "live": {"live_status": 2, "room_id": "r" + user_id, "start_time": 1700000000000,
                     "live_link": "https://x/live?flvUrl=http://cdn/" + user_id + ".flv",
                     "room_extra_info": json.dumps({
                         "live_title": title,
                         "live_stream_info": json.dumps(
                             {"media": {"width": 1080, "height": 1920},
                              "streams": [{"quality_type_name": "原画"}]})})}}


def _idle_user(user_id, nickname):
    return {"user_id": user_id, "nickname": nickname, "desc": ""}


events.clear()
try:
    # 服务端说「登录已过期」：红灯 + warn（不是 error，否则每轮一次托盘）
    events.clear()
    logs.clear()
    cursors = _feed([{"success": False, "msg": "登录已过期"}])
    assert api.fetch_following_users() is None
    assert [l[0] for l in logs] == ["warn"], logs
    assert ("auth", {"expired": True, "message": "服务端说：登录已过期"}) in events, events
    assert cursors == [""], cursors

    # 别的业务错误还是 error，也不该点红灯
    events.clear()
    logs.clear()
    _feed([{"success": False, "msg": "参数不对"}])
    assert api.fetch_following_users() is None
    assert [l[0] for l in logs] == ["error"], logs
    assert not [e for e in events if e[0] == "auth"], events

    # 非 200：fetch_page 记完日志返回 None，这里原样退出（不点红灯，也不交半截列表）
    events.clear()
    logs.clear()
    _feed([None])
    assert api.fetch_following_users() is None
    assert not [e for e in events if e[0] == "auth"], events

    # 翻页 + 跨页去重 + 开播字段 + 档案副作用，一次跑完
    # id 用 t 打头：前面几项测试往档案里写过 u1，换一组干净的 id，档案字段才验得准
    events.clear()
    logs.clear()
    cursors = _feed([
        _ok_page([_live_user("t1", "主播甲"), _idle_user("t2", "主播乙")],
                 cursor="c1", has_more=True),
        _ok_page([_live_user("t1", "主播甲"), _idle_user("t3", "主播丙")]),   # t1 重复出现
    ])
    users = api.fetch_following_users()
    assert cursors == ["", "c1"], cursors
    assert [u["user_id"] for u in users] == ["t1", "t2", "t3"], users
    live = users[0]
    assert live["title"] == "深夜聊天" and live["quality"] == "原画", live
    assert live["resolution"] == "1080×1920" and live["is_live"], live
    assert live["video_url"] == "http://cdn/t1_orig.flv", live     # 拉的是原画流
    assert live["room_id"] == "rt1" and live["desc"] == "个人简介", live
    # 档案里没写过这两项：自动录制默认开、最近录制时间默认空
    assert live["auto_record"] is True and live["last_record"] == "", live
    assert users[1]["is_live"] is False and users[1]["video_url"] == "", users[1]
    assert not written, "轮询路径不该同步落盘：档案写入是防抖的"
    assert ("auth", {"expired": False}) in events, events          # 拉通了，红灯要灭

    # 取不到流地址要说清楚原因，而不是悄悄给个空串
    events.clear()
    logs.clear()
    broken = _live_user("t4", "主播丁")
    broken["live"]["live_link"] = "https://x/live"
    _feed([_ok_page([broken])])
    users = api.fetch_following_users()
    assert users[0]["video_url"] == "" and users[0]["is_live"], users[0]
    assert any("取不到直播流地址" in l[1] for l in logs), logs
finally:
    net.fetch_page = _real_fetch_page
    profiles._write_user_profiles = _real_write_profiles
    settings._ui_settings["sid"] = saved
print("9b 主循环: 翻页 + 去重 + 开播字段 + 登录态两分支 + 非 200，全部走假页面")


# 9c) fetch_page 自己的契约：非 200 要报得出状态码，而且必须带上超时
#     （不给 timeout 的话，网络半死时 requests 会一直挂着，轮询线程再也回不来，
#      而界面那盏「监控中」还亮着 —— 最坏的静默故障）
class _FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code

    def json(self):
        return {"success": True, "data": {}}


calls = []


class _FakeSession:
    def get(self, url, headers=None, params=None, timeout=None):
        calls.append({"params": params, "timeout": timeout})
        return _FakeResponse(500)


_real_session = net._session
settings._ui_settings["sid"] = "fake-sid-for-test-only"
try:
    net._session = lambda: _FakeSession()
    logs.clear()
    assert net.fetch_page("c1") is None
    assert [l[0] for l in logs] == ["error"], logs
    assert "500" in logs[0][1], logs
    assert calls[0]["timeout"] == net.HTTP_TIMEOUT, calls
    assert calls[0]["params"] == {"cursor": "c1", "order": "2"}, calls
finally:
    net._session = _real_session
    settings._ui_settings["sid"] = saved
print("9c fetch_page:", logs[0][1], "· 超时", net.HTTP_TIMEOUT)

# 10) 直播标题藏在 room_extra_info 这层 JSON 字符串里，live 块上根本没有 title
live = {"live_status": 2, "room_id": "r1", "room_extra_info": json.dumps({
    "live_title": "深夜聊天",
    "live_stream_info": json.dumps({"media": {"width": 1080, "height": 1920},
                                    "streams": [{"quality_type_name": "原画"}]}),
})}
assert api._pick_value(api._room_extra(live), "live_title", "title") == "深夜聊天"
assert api._parse_live_stream_info(live)["resolution"] == "1080×1920"
# 主播没填标题时接口给空串（实测大部分房间如此），不能当成解析失败
assert api._pick_value(api._room_extra({"room_extra_info": '{"live_title":""}'}),
                       "live_title", "title") == ""
assert api._room_extra({"room_extra_info": "坏 JSON"}) == {}
assert api._room_extra({}) == {}
print("10 直播标题:", api._pick_value(api._room_extra(live), "live_title", "title"))

# 11) 录制确实落在临时目录，没有污染真实下载目录
assert not os.path.exists(os.path.join(TMP_ROOT, "partial.mkv")), "空头文件应已删除"

# 12) 日志落盘 + 轮转：打包成无控制台 exe 后 stdout 可能是 None，磁盘上必须还有一份副本
#     路径改指临时目录再测，绝不往用户真实的 recorder.log 里写测试行
from xhs import instance                                        # noqa: E402
tmp_cfg = tempfile.mkdtemp(prefix="xhs_log_")
saved_dir, saved_log, saved_max = config.CONFIG_DIR, config.LOG_FILE, config.LOG_MAX_BYTES
config.CONFIG_DIR = tmp_cfg
config.LOG_FILE = os.path.join(tmp_cfg, "recorder.log")
try:
    _orig_emit("第一条 warn 档", "warn")
    _orig_emit("第二条 info 档")
    lines = open(config.LOG_FILE, encoding="utf-8").read().splitlines()
    assert len(lines) == 2, lines                       # 追加，不是每次覆盖
    assert "* 第一条 warn 档" in lines[0], lines[0]      # 级别标记跟着落盘
    assert " 第二条 info 档" in lines[1], lines[1]

    config.LOG_MAX_BYTES = 1                            # 下一行必然越界 → 轮转
    _orig_emit("轮转后的新行", "error")
    assert os.path.exists(config.LOG_FILE + ".1"), "旧日志没转出去"
    old = open(config.LOG_FILE + ".1", encoding="utf-8").read().splitlines()
    new = open(config.LOG_FILE, encoding="utf-8").read().splitlines()
    assert len(old) == 2 and len(new) == 1, (old, new)
    assert "! 轮转后的新行" in new[0], new

    # 写不进去也不能影响录制：把日志路径换成一个目录，open 必然抛异常，这里只该吞掉
    config.LOG_MAX_BYTES = saved_max
    config.LOG_FILE = tmp_cfg
    _orig_emit("这条只出现在终端，不该炸掉调用方")

    # 13) 单实例锁：同一台机器第二次启动必须抢不到，否则两条轮询会各录一份同一场直播
    assert instance.acquire() is True, "空目录里第一次抢锁就该成功"
    assert instance.acquire() is False, "第二次抢锁成功了：两个进程会各录一份"
    # 验 pid 走已持有锁的那把句柄：Windows 的强制区域锁会让「第二个句柄打开同一文件」
    # 直接 PermissionError —— 换个 open() 读它会测出一个现实中不存在的 bug
    instance._lock_handle.seek(0)
    assert instance._lock_handle.read() == str(os.getpid())
finally:
    config.CONFIG_DIR, config.LOG_FILE, config.LOG_MAX_BYTES = saved_dir, saved_log, saved_max
    handle, instance._lock_handle = instance._lock_handle, None   # 测试进程那把锁不是产物，关掉清干净
    if handle is not None:
        handle.close()

print("12 日志落盘:", os.path.basename(saved_log), "+ 轮转 + 抢锁互斥")

# 14) 起 ffmpeg 必须不带控制台：打包版是 console=False 的窗口进程，子进程默认各自开一个
#     黑框，录两路就是两个糊在界面上，用户只能靠手动关（一关就把录制杀了）。
#     这条只能真走一遍 _record_once 的 Popen 调用点，光看常量说明不了它在参数里。
import subprocess as _sp                                          # noqa: E402
spawned = {}


class _FakeStdin:
    def write(self, _chunk): pass
    def close(self): pass


class _FakeFFmpeg:
    stdin = _FakeStdin()
    returncode = 1
    def poll(self): return 1          # 一上来就报"已退出"，只为走最短路径到收尾
    def wait(self, timeout=None): return 1
    def terminate(self): pass
    def kill(self): pass


class _FakeResp:
    def raise_for_status(self): pass
    def iter_content(self, chunk_size=0): return iter([b"x" * 16])
    def close(self): pass


def _fake_popen(_cmd, **kwargs):
    spawned.update(kwargs)
    return _FakeFFmpeg()


real_subprocess = recorder.subprocess
real_session = net._session
recorder.subprocess = types.SimpleNamespace(PIPE=_sp.PIPE, DEVNULL=_sp.DEVNULL,
                                            TimeoutExpired=_sp.TimeoutExpired,
                                            Popen=_fake_popen)
net._session = lambda: types.SimpleNamespace(get=lambda *a, **k: _FakeResp())
try:
    status, _size = _real_record_once(dict(USER, video_url="http://example/flv"),
                                      "k14", os.path.join(TMP_ROOT, "n14.mkv"), 0, 0)
    assert status == recorder.REC_RETRY, status
    assert spawned.get("creationflags") == _sp.CREATE_NO_WINDOW, spawned
    assert spawned.get("stdout") is _sp.DEVNULL, "输出没接走的话管道会写满死锁"
finally:
    recorder.subprocess = real_subprocess
    net._session = real_session
print("14 ffmpeg 子进程:", spawned["creationflags"], "= 不带控制台窗口")

# 15) 实时预览：ffplay 也是控制台程序，同样会带出黑框；而且它比录制更容易被当成
#     「程序卡住了」——所以三件事必须钉住：不带控制台、缺 ffplay 只回话不抛、退出留不下孤儿。
class _FakePlay:
    def __init__(self):
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0


play_calls = []


def _fake_play(cmd, **kwargs):
    play_calls.append((cmd, kwargs))
    return _FakePlay()


real_preview_subprocess = preview.subprocess
preview.subprocess = types.SimpleNamespace(DEVNULL=_sp.DEVNULL, Popen=_fake_play)
saved_ffplay = config.FFPLAY_PATH
try:
    config.FFPLAY_PATH = None
    assert preview.available() is False
    notice = preview.open_preview(dict(USER, video_url="http://example/flv"))
    assert "ffplay" in notice and not play_calls, f"没有 ffplay 却试着起了进程: {notice}"

    config.FFPLAY_PATH = r"C:\ffmpeg\bin\ffplay.exe"
    live = dict(USER, user_id="u15", nickname="主播十五")
    opened = preview.open_preview(live)
    assert opened.startswith("[主播十五]"), opened
    cmd, kwargs = play_calls[0]
    assert kwargs["creationflags"] == _sp.CREATE_NO_WINDOW, "预览会带出一个黑框"
    assert kwargs["stdout"] is _sp.DEVNULL
    assert "-autoexit" in cmd and "预览 · 主播十五" in cmd, cmd
    assert "-noborder" in cmd, "留着系统标题栏就包不成自己的卡片"
    assert cmd[-1] == live["video_url"], "流地址没接在 -i 后面"
    assert preview.is_open("u15") and preview.open_count() == 1
    assert "已经在预览" in preview.open_preview(live) and len(play_calls) == 1, "重复点该拒绝"

    sized = preview.build_command("主播十五", "http://example/flv", size=(540, 960))
    assert sized[sized.index("-x") + 1] == "540", sized
    assert sized[sized.index("-y") + 1] == "960", sized
    # 卡片只管问句柄、摆位置：窗口还没建出来时必须回 0/False/() 而不是抛，
    # 预览起不来是少个画面，不该把界面拖崩
    assert preview.place(0, 0, 0, 10, 10) is False
    assert preview.embed(0, 0, 0, 0, 10, 10) is False
    assert preview.adopt(0, 0, 0, 0) is False
    assert preview.move(0, 0, 0) is False
    assert preview.window_size(0) == ()
    assert preview.set_round_region(0, 100, 100, 8) is False
    assert preview.set_round_region(1234, 100, 100, 60) is False   # 半径大到装不下也不该硬上
    preview.set_visible(0, True)          # 没句柄也不该炸

    preview._open["u15"].proc.returncode = 0      # 断流后 ffplay 会自己退出
    assert not preview.is_open("u15"), "窗口早自关了还报「在预览」，菜单就永远停在「停止预览」"
    assert preview.open_count() == 0

    assert preview.open_preview(dict(live, user_id="u16", nickname="主播十六"))
    assert preview.open_preview(dict(live, user_id="u17", nickname="主播十七"))
    assert preview.close("u16") is True and preview.close("u16") is False
    assert preview.close_all() == 1 and preview._open == {}, "退出时不该留下预览窗口"
    assert preview.close("u99") is False
finally:
    preview.subprocess = real_preview_subprocess
    config.FFPLAY_PATH = saved_ffplay
    preview._open.clear()

# _find_tool 的第三档：ffplay 未必单独进 PATH，但它一定躺在 ffmpeg 旁边
find_tmp = tempfile.mkdtemp(prefix="xhs_tool_")
os.makedirs(os.path.join(find_tmp, "bin"), exist_ok=True)
sibling_ffplay = os.path.join(find_tmp, "bin", "xhs_zzffplay.exe")
with open(sibling_ffplay, "wb"):
    pass
saved_program_dir = config.PROGRAM_DIR
try:
    config.PROGRAM_DIR = find_tmp       # 程序目录里也没有，只能靠同门那一档
    assert config._find_tool("xhs_zzffmpeg", sibling_of=sibling_ffplay) is None
    assert config._find_tool("xhs_zzffplay", sibling_of=sibling_ffplay) == sibling_ffplay
finally:
    config.PROGRAM_DIR = saved_program_dir
print("15 实时预览: ffplay 子进程不带控制台 + 缺件只回话 + 退场不留孤儿窗口")

# 收尾这句走 print 而不是 emit_log：测试不该往用户真实的 recorder.log 里写行
print(f"[{utils.current_time_formatted()}] 核心层冒烟测试全部通过（录制目录: {TMP_ROOT}）")
