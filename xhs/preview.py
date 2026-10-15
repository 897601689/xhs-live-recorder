"""实时预览：把直播流交给 ffplay，窗口由界面层包成我们自己的卡片

为什么不自己画帧：解码、音画同步、管道背压、退出收尾全得自己管一遍，那份成本远超
"预览"在这款工具里该有的分量。ffplay 和 ffmpeg 出自同一个包，而录制本来就要求
ffmpeg 在场，所以它几乎是零新增依赖就能拿到真视频 + 声音。

代价要说清楚：预览是**第二条**打到同一直播间的拉流连接（录制已经占了一条）。
所以这里只跟着用户主动点，不做自动开、不做断流自动续 —— 那是录制该干的事。

这一层只管进程和它的窗口句柄；边框、标题栏、拖动在 ui/preview_window.py。
ffplay 自己只能给到「无边框 + 指定尺寸」，主题一律管不着，所以外观只能由外层画。
"""

import ctypes
import ctypes.wintypes as wt
import subprocess
import sys

from xhs import config

SDL_WINDOW_CLASS = "SDL_app"
_user32 = ctypes.windll.user32 if sys.platform == "win32" else None
_gdi32 = ctypes.windll.gdi32 if sys.platform == "win32" else None
if _user32 is not None:
    _user32.SetWindowRgn.argtypes = [wt.HWND, wt.HRGN, ctypes.c_int]
    _gdi32.CreateRoundRectRgn.restype = wt.HRGN
    _gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6

# 嵌进卡片时要剥掉的那几样：ffplay 是 -noborder 起的，但 SDL 仍可能带着这些位
_WS_CAPTION, _WS_BORDER, _WS_DLGFRAME = 0x00C00000, 0x00800000, 0x00400000
_WS_THICKFRAME, _WS_POPUP, _WS_CHILD = 0x00040000, 0x80000000, 0x40000000
_GWL_STYLE = -16
_SWP_FRAMECHANGED, _SWP_NOZORDER, _SWP_NOSIZE = 0x0020, 0x0004, 0x0001
_SW_HIDE, _SW_SHOW = 0, 5


class _Session:
    """一路预览 = 一个 ffplay 进程 + 那个我们嵌进来的窗口"""

    __slots__ = ("proc", "hwnd")

    def __init__(self, proc):
        self.proc = proc
        self.hwnd = 0     # 窗口是 ffplay 异步建的，第一次去问多半还没有


_open = {}        # user_id -> _Session；进程在不在是唯一判据，不看别的


def available() -> bool:
    """这台机器上有没有 ffplay：没有只是没预览，录制照旧"""
    return bool(config.FFPLAY_PATH)


def is_open(user_id) -> bool:
    """该主播的预览还开着吗（顺带回收已经自己退掉的）"""
    return _reap(str(user_id))


def _reap(user_id: str) -> bool:
    session = _open.get(user_id)
    if session is None:
        return False
    if session.proc.poll() is not None:
        # 流断了 ffplay 会自己退出（-autoexit），这里不收就会一直谎报"在预览"
        _open.pop(user_id, None)
        return False
    return True


def build_command(nickname: str, url: str, size=None) -> list:
    """拼 ffplay 的参数

    -nobuffer + -framedrop：预览要的是"现在"，不是攒三秒再放；解码跟不上就丢帧，
      不然延迟会一路越积越大，看着像卡住。
    -noborder：Windows 那套原生标题栏跟主程序不是一套东西，只能去掉再由外层画卡片。
    -x / -y：显示尺寸直接给成外层内容区的大小（物理像素）。不给的话 ffplay 按流的
      原始分辨率开自己的尺寸，嵌进卡片就是画面缩在角落里。
    -window_title：嵌好之后标题栏归我们画，这个值只在上屏探针里看得到 —— 留着是为了
      排查「画面到底进没进卡片」。
    -autoexit：下播后进程自己退，不用用户挨个去点叉。
    """
    command = [config.FFPLAY_PATH,
               "-hide_banner", "-loglevel", "error",
               "-fflags", "nobuffer", "-framedrop", "-autoexit", "-noborder",
               "-window_title", f"预览 · {nickname}"]
    if size:
        command += ["-x", str(int(size[0])), "-y", str(int(size[1]))]
    return command + ["-i", url]


def open_preview(user_info: dict, size=None) -> str:
    """开一路预览，回报一句给用户看的话

    这里不抛异常：预览起不来只是少个窗口，不该把点菜单这个动作变成一次报错。
    """
    user_id = str(user_info.get("user_id") or "")
    nickname = user_info.get("nickname") or user_id or "该主播"
    if not available():
        return ("没找到 ffplay.exe，无法预览：它和 ffmpeg 在同一个包里，"
                "把整个 bin 目录加进 PATH、或把 ffplay.exe 放到程序同目录即可")
    if _reap(user_id):
        return f"[{nickname}] 已经在预览了，关掉那个窗口再开新的"
    url = user_info.get("video_url") or ""
    if not url:
        return f"[{nickname}] 没有直播流地址，刷新一下关注列表再试"
    try:
        proc = subprocess.Popen(build_command(nickname, url, size),
                                stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                creationflags=config.NO_CONSOLE)
    except OSError as exc:
        return f"[{nickname}] 预览启动失败: {exc}"
    _open[user_id] = _Session(proc)
    return f"[{nickname}] 预览已打开（多占一路拉流），关掉窗口即停止"


def find_hwnd(user_id) -> int:
    """这一路预览的 SDL 顶层窗口句柄；还没建出来返回 0，调用方隔一会儿再问

    ffplay 的窗口归它自己的事件循环管，建出来是异步的 —— 只能问不能等，等就把界面
    卡住了。句柄拿到一次就缓存，后面不用每次重新枚举整屏窗口。
    """
    session = _open.get(str(user_id))
    if session is None or _user32 is None:
        return 0
    if session.hwnd and _user32.IsWindow(session.hwnd):
        return session.hwnd
    session.hwnd = _enum_sdl_window(session.proc.pid)
    return session.hwnd


def _enum_sdl_window(pid: int) -> int:
    """按进程找那个 SDL 窗口：同时开两路预览时只能按 pid 分，不能按标题猜"""
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def callback(hwnd, _lparam):
        owner = wt.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value != pid:
            return True
        name = ctypes.create_unicode_buffer(64)
        _user32.GetClassNameW(hwnd, name, 64)
        if name.value != SDL_WINDOW_CLASS:
            return True
        found.append(hwnd)
        return False

    _user32.EnumWindows(callback, 0)
    return found[0] if found else 0


def window_size(child_hwnd) -> tuple:
    """它现在多大（物理像素）；窗口没了返回空 —— 卡片靠这个判断 ffplay 改完尺寸没有"""
    if _user32 is None or not child_hwnd or not _user32.IsWindow(child_hwnd):
        return ()
    box = wt.RECT()
    _user32.GetWindowRect(wt.HWND(child_hwnd), ctypes.byref(box))
    return box.right - box.left, box.bottom - box.top


def set_round_region(hwnd, width: int, height: int, radius: int) -> bool:
    """按设备像素给这张窗口上圆角裁剪区域（宽高半径都是物理像素）

    别改回 Qt 的 setMask：那份区域只有逻辑像素的精度，Windows 再按缩放放大一倍，
    200% 下每一档台阶就是 2 物理像素 —— 12 逻辑半径的圆弧只剩 7 档，角上看着是一串点。
    区域交出去就归系统所有，不能再 DeleteObject；只要不去 setMask，Qt 也不会来抢。
    """
    if _user32 is None or not hwnd or radius <= 0 or width <= 2 * radius or height <= 2 * radius:
        return False
    region = _gdi32.CreateRoundRectRgn(0, 0, width + 1, height + 1, radius * 2, radius * 2)
    if not region:
        return False
    return _user32.SetWindowRgn(wt.HWND(hwnd), region, 1) >= 0


def set_visible(child_hwnd, visible: bool) -> None:
    """藏一藏 / 露出来：窗口归 ffplay 的事件循环管，只能跟 Windows 打招呼"""
    if _user32 is not None and child_hwnd:
        _user32.ShowWindow(wt.HWND(child_hwnd), _SW_SHOW if visible else _SW_HIDE)


def move(child_hwnd, x: int, y: int) -> bool:
    """只挪位置，尺寸一个像素都不动 —— 为什么必须这样，见 adopt"""
    if _user32 is None or not child_hwnd:
        return False
    return bool(_user32.SetWindowPos(wt.HWND(child_hwnd), 0, x, y, 0, 0,
                                     _SWP_NOSIZE | _SWP_FRAMECHANGED | _SWP_NOZORDER))


def place(child_hwnd: int, x: int, y: int, width: int, height: int) -> bool:
    """把 SDL 窗口摆成卡片内容区那块（物理像素），尺寸一起改

    只能当兜底用：见 adopt 的说明，替 ffplay 改尺寸会让它的画面偏掉。
    """
    if _user32 is None or not child_hwnd:
        return False
    return bool(_user32.SetWindowPos(wt.HWND(child_hwnd), 0, x, y, width, height,
                                     _SWP_FRAMECHANGED | _SWP_NOZORDER))


def _adopt(parent_hwnd: int, child_hwnd: int) -> bool:
    """SetParent + 剥边框：从这里起它就是卡片里的一块内容

    SetParent 之后它不再是顶层窗口：卡片隐藏它跟着隐藏，卡片关了它也不会留在桌面上。
    """
    if _user32 is None or not parent_hwnd or not child_hwnd:
        return False
    _user32.SetParent(wt.HWND(child_hwnd), wt.HWND(parent_hwnd))
    style = _user32.GetWindowLongW(child_hwnd, _GWL_STYLE) & 0xFFFFFFFF
    _user32.SetWindowLongW(child_hwnd, _GWL_STYLE,
                           (style & ~(_WS_CAPTION | _WS_BORDER | _WS_DLGFRAME
                                      | _WS_THICKFRAME | _WS_POPUP)) | _WS_CHILD)
    return True


def adopt(parent_hwnd: int, child_hwnd: int, x: int, y: int) -> bool:
    """认卡片当爹，尺寸留给 ffplay 自己定

    顺序很要紧：ffplay 解出首帧会自己把窗口开成 -x/-y 那一档，并在同一刻按那个尺寸算好
    画面摆在哪。抢在它前头替它改尺寸，它的渲染视口还停在旧尺寸上，画面就被放大着画出去、
    右边切掉一截（实测 1004 宽整幅右移 19 像素，看着像「没缩放、只露了一部分」）。
    非 Windows 或句柄没建出来时返回 False，调用方照常显示一张空卡片就行。
    """
    if not _adopt(parent_hwnd, child_hwnd):
        return False
    return move(child_hwnd, x, y)


def embed(parent_hwnd: int, child_hwnd: int, x: int, y: int,
          width: int, height: int) -> bool:
    """认爹之外连尺寸一起改：等不到 ffplay 自己变大时的兜底，画面可能偏一档"""
    if not _adopt(parent_hwnd, child_hwnd):
        return False
    return place(child_hwnd, x, y, width, height)


def close(user_id) -> bool:
    """关掉某个主播的预览；返回是否真关了一个"""
    session = _open.pop(str(user_id), None)
    if session is None:
        return False
    if session.proc.poll() is None:
        session.proc.terminate()
    return True


def close_all() -> int:
    """退出时收掉所有预览窗口：留一堆孤儿窗口在桌面上算怎么回事"""
    count = len(_open)
    for user_id in list(_open):
        close(user_id)
    return count


def open_count() -> int:
    """当前还开着的预览窗口数（先回收再数，不然报的是历史累计）"""
    for user_id in list(_open):
        _reap(user_id)
    return len(_open)
