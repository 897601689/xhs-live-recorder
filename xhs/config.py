"""运行常量与路径：输出目录默认值、平台名、ffmpeg 探测、配置文件位置、界面默认偏好"""

import os
import shutil
import subprocess
import sys

from xhs import utils


# ============================================================
# 运行配置（如需修改输出位置/ffmpeg 路径，改这里即可）
# ============================================================
# 下载根目录默认值：用户“下载”文件夹下的“小红书直播”（实际生效值见 output_root()）
DEFAULT_OUTPUT_ROOT = os.path.join(os.path.expanduser("~"), "Downloads", "小红书直播")

# 版本号：标题栏那枚小字显示它，报问题时能一眼对上代码状态
APP_VERSION = "1.0"

# 平台显示名，同时用作保存路径中的 {platform} 标签
PLATFORM_NAME = "小红书"
LEGACY_PLATFORM_DIR = "xhs"    # 旧版本使用的平台目录名（用于兼容已存在的旧目录）


# ffmpeg：先看系统 PATH，再看「程序自己所在的目录」
# 不能写 "./ffmpeg"：相对的是当前工作目录，双击 exe 时那往往是 System32，
# 于是文件明明躺在 exe 旁边却永远找不到。
PROGRAM_DIR = (os.path.dirname(os.path.abspath(sys.executable))
               if getattr(sys, "frozen", False) else utils.PROJECT_ROOT)


def _find_tool(name: str, sibling_of=None):
    """按 PATH → 程序目录 → 已找到的同门工具旁边 的顺序找一个外部可执行文件

    第三档是给「把 ffmpeg.exe 扔在 exe 同目录」这种装法留的：ffplay 通常和 ffmpeg
    在同一个 bin 里，但那台机器上未必把整个 bin 加进了 PATH。
    """
    found = shutil.which(name)
    if found:
        return found
    candidates = [os.path.join(PROGRAM_DIR, stem) for stem in (name + ".exe", name)]
    if sibling_of:
        candidates += [os.path.join(os.path.dirname(sibling_of), stem)
                       for stem in (name + ".exe", name)]
    return next((path for path in candidates if os.path.exists(path)), None)


FFMPEG_PATH = _find_tool("ffmpeg")
if FFMPEG_PATH is None:
    print(f"[{utils.current_time_formatted()}] 警告: 未找到 ffmpeg,下载功能将不可用,"
          f"请将其加入系统 PATH 或放在 {PROGRAM_DIR}")
# 实时预览用 ffplay（和 ffmpeg 同一个包）：找不到只是没预览，录制照旧
FFPLAY_PATH = _find_tool("ffplay", sibling_of=FFMPEG_PATH)


# 起子进程时不要控制台窗口：本程序打包后是 console=False 的窗口进程，父方自己没有
# 控制台可依附，Windows 就会给每个控制台子程序新建一个黑框（录两路就是两个黑框糊在
# 界面上）。非 Windows 上没这个常量，退成 0 等于不传。
NO_CONSOLE = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# 中断收尾的可播放下限：小于此体积的中断文件没有保留价值（几 KB 的空头）
PARTIAL_MIN_BYTES = 1024 * 1024
# 磁盘余量：开录前要求 500MB，录制中低于 120MB 主动收尾，避免写出废文件
DISK_MIN_FREE_START = 500 * 1024 * 1024
DISK_MIN_FREE_KEEP = 120 * 1024 * 1024
# 断流后最多重连续录的次数（每次退避 2/5/10 秒，之后固定 10 秒）
RECORD_MAX_RESTARTS = 20
RECORD_RESTART_WAITS = (2, 5, 10)


CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".xhs_live_recorder")
USER_PROFILES_FILE = os.path.join(CONFIG_DIR, "user_profiles.json")
LEGACY_AUTO_RECORD_FILE = os.path.join(CONFIG_DIR, "auto_record.json")
LEGACY_LAST_RECORD_FILE = os.path.join(CONFIG_DIR, "last_record.json")

# 日志落盘：打包成无控制台 exe 后 stdout 可能是 None，界面抽屉又是内存里的，
# 凌晨断流第二天起来就死无对证 —— 所以必须有一行行落在磁盘上的副本。
LOG_FILE = os.path.join(CONFIG_DIR, "recorder.log")
LOG_MAX_BYTES = 1024 * 1024          # 单文件到点转成 recorder.log.1，只留一份旧的


PROFILES_FLUSH_DELAY = 5.0


# 登录态：唯一被服务端校验的字段（会话 ID），可在设置界面更新，存 ui_settings.json
# 等同于账号密码，请勿外传；失效时接口返回 200 + success:false + msg="登录已过期"
# 这里刻意留空：真实 sid 只落在 ~/.xhs_live_recorder/ui_settings.json 或环境变量
# XHS_SID 里，源码与打包产物都不带凭证（否则发出去的就是别人的账号）。
DEFAULT_SID = ""
SID_ENV_VAR = "XHS_SID"


# ============================================================
# 轮询控制：终端模式与 GUI 共用同一份主循环
# ============================================================
POLL_MIN_INTERVAL = 30      # 默认轮询下限（用户改过则以设置里的值为准，见 current_poll_interval）
POLL_MAX_INTERVAL = 90      # 默认轮询上限
POLL_MIN_LIMIT, POLL_MAX_LIMIT = 5, 3600        # 可调范围，与设置界面的 SpinBox 保持一致

UI_SETTINGS_FILE = os.path.join(CONFIG_DIR, "ui_settings.json")

THEME_LIGHT = "light"
THEME_DARK = "dark"
DEFAULT_THEME = THEME_LIGHT                      # 默认使用明亮主题
THEME_NAMES = {THEME_LIGHT: "明亮", THEME_DARK: "深色"}

DEFAULT_NAME_TEMPLATE = "{platform}/{owner}/{owner}-{year}-{month}-{date} {hour}-{min}-{sec}"

# 关注列表每页显示数量：数字为每页条数，"全部"表示不分页
# 30 / 40 是给「一页看完但不想太挤」留的中间档；界面层显示成「30条/页」，这里存值
PAGE_SIZE_CHOICES = ("10", "20", "30", "40", "50", "100", "全部")
PAGE_SIZE_ALL = "全部"
DEFAULT_PAGE_SIZE = 20

# 关注列表显示模式：卡片 / 列表
VIEW_MODES = ("card", "list")
DEFAULT_VIEW_MODE = "list"

# 关注列表「显示字段」：列表视图可开关的列，顺序即列顺序；「用户」列固定最前不可关
# （nick / remark 是用户列内部的子字段：主播名 / 备注）
LIST_FIELDS = (("platform", "平台"), ("live", "直播状态"), ("record", "录制状态"),
               ("room", "房间号"), ("last", "最近录制时间"), ("action", "操作"),
               ("nick", "主播名"), ("remark", "备注"))
DEFAULT_LIST_FIELDS = tuple(key for key, _caption in LIST_FIELDS)
