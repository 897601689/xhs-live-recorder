"""界面偏好读写（ui_settings.json）与文件命名规则渲染"""

import json
import os
import re
import urllib
from datetime import datetime

from xhs import config, state, utils


# 界面偏好：主题 / 轮询间隔 / 输出目录 / 文件命名规则 / 每页数量 / 登录态，统一落在 ui_settings.json
_ui_settings = {
    "theme": config.DEFAULT_THEME,
    "poll_min": config.POLL_MIN_INTERVAL,
    "poll_max": config.POLL_MAX_INTERVAL,
    "output_dir": config.DEFAULT_OUTPUT_ROOT,
    "name_template": config.DEFAULT_NAME_TEMPLATE,
    "page_size": config.DEFAULT_PAGE_SIZE,
    "view_mode": config.DEFAULT_VIEW_MODE,
    "sid": config.DEFAULT_SID,
}


def _clamp_poll(value, fallback: int) -> int:
    """轮询间隔：非法值或超出可调范围时回落到默认值"""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return min(max(number, config.POLL_MIN_LIMIT), config.POLL_MAX_LIMIT)


def _clamp_page_size(value) -> int:
    """每页数量：只接受可选额度内的数字，其余（含 0 / 全部 / 非法值）回落到默认值"""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return config.DEFAULT_PAGE_SIZE
    return number if str(number) in config.PAGE_SIZE_CHOICES else config.DEFAULT_PAGE_SIZE


def load_ui_settings() -> None:
    """启动时恢复界面偏好（主题 / 轮询间隔 / 输出目录 / 文件命名规则 / 每页数量）"""
    try:
        with open(config.UI_SETTINGS_FILE, "r", encoding="utf-8") as fp:
            data = json.load(fp)
    except FileNotFoundError:
        return
    except Exception as exc:
        state.emit_log(f"界面设置读取失败: {exc}", level="warn")
        return
    if not isinstance(data, dict):
        return
    theme = data.get("theme")
    if theme in config.THEME_NAMES:
        _ui_settings["theme"] = theme
    _ui_settings["poll_min"] = _clamp_poll(data.get("poll_min"), config.POLL_MIN_INTERVAL)
    _ui_settings["poll_max"] = _clamp_poll(data.get("poll_max"), config.POLL_MAX_INTERVAL)
    if _ui_settings["poll_max"] < _ui_settings["poll_min"]:
        _ui_settings["poll_min"], _ui_settings["poll_max"] = (
            _ui_settings["poll_max"], _ui_settings["poll_min"])
    output_dir = data.get("output_dir")
    if isinstance(output_dir, str) and output_dir.strip():
        _ui_settings["output_dir"] = output_dir.strip()
    name_template = data.get("name_template")
    if isinstance(name_template, str) and name_template.strip():
        _ui_settings["name_template"] = name_template.strip()
    page_size = data.get("page_size", config.DEFAULT_PAGE_SIZE)
    # 0 / "全部" 表示不分页，其余按可选额度校验
    _ui_settings["page_size"] = (0 if page_size in (0, "0", config.PAGE_SIZE_ALL)
                                 else _clamp_page_size(page_size))
    view_mode = data.get("view_mode")
    if view_mode in config.VIEW_MODES:
        _ui_settings["view_mode"] = view_mode
    stored_fields = data.get("list_fields")
    if isinstance(stored_fields, list):
        _ui_settings["list_fields"] = [key for key in config.DEFAULT_LIST_FIELDS
                                       if key in stored_fields]
    sid = data.get("sid")
    if isinstance(sid, str) and sid.strip():
        _ui_settings["sid"] = sid.strip()


def save_ui_settings() -> None:
    """界面偏好整体落盘（原子写，避免写一半被打断导致设置丢失）"""
    try:
        os.makedirs(config.CONFIG_DIR, exist_ok=True)
        temp_path = config.UI_SETTINGS_FILE + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as fp:
            json.dump(_ui_settings, fp, ensure_ascii=False, indent=2)
        os.replace(temp_path, config.UI_SETTINGS_FILE)
    except Exception as exc:
        state.emit_log(f"界面设置保存失败: {exc}", level="warn")


def current_theme() -> str:
    return _ui_settings.get("theme") or config.DEFAULT_THEME


def output_root() -> str:
    """当前录制输出根目录（设置里的值，随「浏览」切换即时生效）"""
    return _ui_settings.get("output_dir") or config.DEFAULT_OUTPUT_ROOT


def current_poll_interval() -> tuple:
    """当前轮询间隔 (最小, 最大)，秒"""
    return (_ui_settings.get("poll_min", config.POLL_MIN_INTERVAL),
            _ui_settings.get("poll_max", config.POLL_MAX_INTERVAL))


def set_theme(name: str) -> str:
    """切换主题并立即落盘（下次启动保持），返回生效后的主题名"""
    _ui_settings["theme"] = name if name in config.THEME_NAMES else config.DEFAULT_THEME
    save_ui_settings()
    return _ui_settings["theme"]


def set_poll_interval(min_interval, max_interval) -> None:
    """保存轮询间隔，下次启动沿用"""
    _ui_settings["poll_min"] = _clamp_poll(min_interval, config.POLL_MIN_INTERVAL)
    _ui_settings["poll_max"] = _clamp_poll(max_interval, config.POLL_MAX_INTERVAL)
    if _ui_settings["poll_max"] < _ui_settings["poll_min"]:
        _ui_settings["poll_min"], _ui_settings["poll_max"] = (
            _ui_settings["poll_max"], _ui_settings["poll_min"])
    save_ui_settings()


def set_output_dir(path: str) -> None:
    """保存输出目录，下次启动沿用"""
    if not path:
        return
    _ui_settings["output_dir"] = path
    save_ui_settings()


def current_page_size() -> int:
    """每页显示数量（0 表示不分页 / 全部）"""
    value = _ui_settings.get("page_size", config.DEFAULT_PAGE_SIZE)
    return 0 if value in (0, "0", config.PAGE_SIZE_ALL) else _clamp_page_size(value)


def set_page_size(value) -> int:
    """保存每页显示数量，下次启动沿用；返回生效后的值"""
    number = 0 if value in (None, "", config.PAGE_SIZE_ALL) else _clamp_page_size(value)
    _ui_settings["page_size"] = number
    save_ui_settings()
    return number


def current_view_mode() -> str:
    """关注列表显示模式：card / list"""
    return _ui_settings.get("view_mode") or config.DEFAULT_VIEW_MODE


def set_view_mode(mode: str) -> str:
    """保存关注列表显示模式，下次启动沿用；返回生效后的模式"""
    _ui_settings["view_mode"] = mode if mode in config.VIEW_MODES else config.DEFAULT_VIEW_MODE
    save_ui_settings()
    return _ui_settings["view_mode"]


def current_list_fields() -> tuple:
    """当前显示的字段（按 LIST_FIELDS 顺序过滤）；非法输入回落到全显示"""
    stored = _ui_settings.get("list_fields")
    if not isinstance(stored, (list, tuple)):
        return config.DEFAULT_LIST_FIELDS
    picked = tuple(key for key, _caption in config.LIST_FIELDS if key in stored)
    return picked or config.DEFAULT_LIST_FIELDS   # 一个不剩时恢复默认，避免整排空白


def set_list_fields(fields) -> tuple:
    """保存列表显示字段，下次启动沿用；返回生效后的字段元组"""
    wanted = set(fields or ())
    picked = list(key for key, _caption in config.LIST_FIELDS if key in wanted)
    _ui_settings["list_fields"] = picked or list(config.DEFAULT_LIST_FIELDS)
    save_ui_settings()
    return tuple(_ui_settings["list_fields"])


def current_sid() -> str:
    """当前登录态（会话 ID）：接口唯一校验的凭证；设置文件优先，其次环境变量 XHS_SID"""
    sid = str(_ui_settings.get("sid") or "").strip()
    if not sid:
        sid = (os.environ.get(config.SID_ENV_VAR) or "").strip()
        if sid:
            _ui_settings["sid"] = sid
    return sid


# 抓包内容里的 sid 形如 sid=session.0000000000000000000000（这串是假的占位）。
# 这里曾经是真实登录态的原值 —— 它跟着源码提交进了仓库，等于凭证躺在代码里。
# 2026-10-14 整个仓库重建成单条 1.0 提交，历史里那份副本随之消失；但值本身仍在那次会话输出里，
# 所以真正要紧的还是去把 sid 换掉。
_SID_IN_PARAMS = re.compile(r"sid=([^&\s;\"'）)，,]+)", re.IGNORECASE)
_SID_BARE = re.compile(r"\b(session\.[A-Za-z0-9_\-]+)")


def extract_sid(text: str) -> str:
    """从整段抓包文本里抠出 sid 值

    支持三种粘贴形态，由严到宽依次回落：
    1) 完整的 xy-common-params / URL query —— 取 sid= 后面的值（顺带还原 URL 编码）；
    2) 一整行抓包报文里夹着的一个 session.xxx；
    3) 直接就是 sid 本身，原样返回。
    """
    raw = (text or "").strip()
    if not raw:
        return ""
    for pattern in (_SID_IN_PARAMS, _SID_BARE):
        match = pattern.search(raw)
        if match is not None:
            return urllib.parse.unquote(match.group(1)).strip()
    return raw


def set_sid(sid: str) -> str:
    """保存登录态，下次启动沿用；返回生效后的值（粘进整段抓包内容时自动取 sid）"""
    _ui_settings["sid"] = extract_sid(sid)
    save_ui_settings()
    return _ui_settings["sid"]


def current_name_template() -> str:
    return _ui_settings.get("name_template") or config.DEFAULT_NAME_TEMPLATE


def set_name_template(template: str) -> None:
    """保存文件命名规则，下次录制沿用"""
    _ui_settings["name_template"] = template.strip() or config.DEFAULT_NAME_TEMPLATE
    save_ui_settings()


NAME_TEMPLATE_TAG_RE = re.compile(r"\{([^{}]+)\}")
NAME_TEMPLATE_FIELDS = ("platform", "channelId", "remarks", "owner", "title",
                        "year", "month", "date", "hour", "min", "sec", "ms")


def validate_name_template(template: str) -> tuple:
    """校验命名规则：返回 (是否有效, 提示信息)"""
    template = (template or "").strip()
    if not template:
        return False, "命名规则不能为空"
    tags = NAME_TEMPLATE_TAG_RE.findall(template)
    if not tags:
        return False, "命名规则中至少要包含一个 {标签}，例如 {owner}"
    unknown = [tag for tag in tags if tag not in NAME_TEMPLATE_FIELDS]
    if unknown:
        return False, "未知标签: " + "、".join(f"{{{tag}}}" for tag in unknown)
    # 试渲染一遍：确认生成结果文件名非空
    _relative_dir, file_base = render_name_template(template, {"nickname": "测试", "room_id": "1"})
    if not file_base:
        return False, "命名规则渲染后文件名为空，请检查标签组合"
    return True, "命名规则有效"


def render_name_template(template: str, user_info: dict) -> tuple:
    """按命名规则渲染出（相对目录, 文件名主体），不含扩展名。

    可用标签：{platform} {channelId} {remarks} {owner} {title}
              {year} {month} {date} {hour} {min} {sec} {ms}
    未知标签会被剔除，"/" 分隔出子目录，非法文件名字符会被过滤。
    """
    now = datetime.now()
    fields = {
        "platform": config.PLATFORM_NAME,
        "channelId": utils.sanitize_filename(str(user_info.get("room_id") or user_info.get("user_id") or "")),
        "remarks": utils.sanitize_filename(str(user_info.get("remark") or user_info.get("nickname") or "")),
        "owner": utils.sanitize_filename(str(user_info.get("nickname") or "unknown")),
        "title": utils.sanitize_filename(str(user_info.get("title") or user_info.get("desc") or "")),
        "year": f"{now:%Y}",
        "month": f"{now:%m}",
        "date": f"{now:%d}",
        "hour": f"{now:%H}",
        "min": f"{now:%M}",
        "sec": f"{now:%S}",
        "ms": f"{now.microsecond // 1000:03d}",
    }
    rendered = (template or "").strip() or config.DEFAULT_NAME_TEMPLATE
    rendered = rendered.replace("\\", "/")
    for key, value in fields.items():
        rendered = rendered.replace("{" + key + "}", value)
    rendered = NAME_TEMPLATE_TAG_RE.sub("", rendered)   # 剔除未知标签
    parts = [utils.sanitize_filename(part) for part in rendered.split("/")]
    # 空标签（如未取到标题的 {title}）会留下多余分隔符，折叠并去掉首尾分隔符
    parts = [re.sub(r"[\s\-_]{2,}", lambda m: m.group(0)[0], part).strip(" -_") for part in parts]
    parts = [part for part in parts if part]
    if not parts:
        parts = [fields["owner"], f"{fields['owner']}-{fields['year']}{fields['month']}{fields['date']} "
                                  f"{fields['hour']}-{fields['min']}-{fields['sec']}"]
    return "/".join(parts[:-1]), parts[-1]


# 启动时恢复界面偏好（轮询间隔 / 输出目录通过 output_root()、current_poll_interval() 读取）
load_ui_settings()
