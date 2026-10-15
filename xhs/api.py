"""直播信息接口：拉取关注列表、解析开播信息与录制地址"""

import json
import requests
import time
import urllib

from xhs import net, profiles, recorder, settings, state, utils


def extract_flv_urls(live_link):
    """
    从直播链接中提取并解码FLV视频URL

    Args:
        live_link (str): 直播链接地址

    Returns:
        str: 解码后的FLV视频URL

    Raises:
        ValueError: 链接缺少 query 或其中没有 flvUrl（由调用方记日志，不再静默变成空地址）
    """
    query = live_link.split('?')[1] if '?' in live_link else ""
    if not query:
        raise ValueError("直播链接没有 query 参数")
    flv_urls = urllib.parse.parse_qs(query).get('flvUrl') or []
    if not flv_urls:
        raise ValueError("直播链接里没有 flvUrl")
    return urllib.parse.unquote(flv_urls[0])


def _pick_value(mapping, *keys, default=""):
    """按优先级取出映射中第一个非空字段值（接口字段名在不同版本会有差异）"""
    if not isinstance(mapping, dict):
        return default
    for key in keys:
        value = mapping.get(key)
        if value not in (None, "", [], {}):
            return value
    return default


def _room_extra(live: dict) -> dict:
    """取出 room_extra_info：它是个 JSON 字符串，直播标题和流信息都塞在里面

    live 块本身没有 title / room_title / name 任何一个，标题只在这层里（实测字段
    live_title）。主播没填时接口给的是空串，不是缺字段。
    """
    extra = live.get("room_extra_info") or "{}"
    if isinstance(extra, str):
        try:
            extra = json.loads(extra)
        except Exception:
            return {}
    return extra if isinstance(extra, dict) else {}


def _parse_live_stream_info(live: dict) -> dict:
    """解析 room_extra_info 中的直播流信息，取画质与分辨率（解析失败返回空字典）"""
    try:
        extra = _room_extra(live)
        stream = extra.get("live_stream_info") or "{}"
        if isinstance(stream, str):
            stream = json.loads(stream)
        media = stream.get("media") or {}
        streams = stream.get("streams") or []
        quality = _pick_value(streams[0], "quality_type_name") if streams else ""
        width = int(media.get("width") or 0)
        height = int(media.get("height") or 0)
        return {
            "quality": quality or "原画",
            "width": width,
            "height": height,
            "resolution": f"{width}×{height}" if width and height else "",
        }
    except Exception:
        return {}


def fetch_following_users():
    """获取关注列表中的**全部**用户（含未开播），程序启动与轮询共用

    Returns:
        list[dict]: 每位用户包含 nickname / user_id / remark / avatar / fan_count /
                    is_live / room_id / room_start_time / quality / resolution /
                    video_url / last_record；请求失败返回 None

    请求本身在 net.fetch_page 里：这里只管翻页、去重和登录态语义，
    测试喂一串假页面就能覆盖整条循环。
    """
    try:
        if not settings.current_sid():
            # 一次都没配过：与「过期」走同一个红灯状态，修法也一样（设置 → 登录态）
            state.notify_ui("auth", {"expired": True, "message": "还没有配置登录态 sid"})
            state.emit_log("未配置登录态 sid：打开「设置 → 登录态」粘贴 sid 后才能拉取关注列表",
                     level="warn")
            return None
        cursor = ""
        has_more = True
        result = []
        seen_user_ids = set()  # 跨页去重，同一用户不会重复出现
        while has_more:
            json_data = net.fetch_page(cursor)
            if json_data is None:
                return None     # 非 200：fetch_page 已经把状态码写进日志了

            # 检查返回的数据是否有错误
            if "success" in json_data and not json_data["success"]:
                msg = json_data.get("msg") or "未知错误"
                # 实测：sid 失效时服务端返回 200 + success:false + msg="登录已过期"
                if "登录" in msg:
                    # 红灯 + 托盘只弹一次（上升沿）；日志每轮照记，作为「还在失败」的证据。
                    # 级别用 warn 而不是 error：error 会让界面每条都弹一次托盘，
                    # 轮询间隔 30 秒就是每半分钟一次，直到用户重启程序。
                    state.notify_ui("auth", {"expired": True, "message": f"服务端说：{msg}"})
                    state.emit_log(f"登录态已失效（{msg}）：请重新抓取并更新 xy-common-params 里的 sid",
                             level="warn")
                else:
                    state.emit_log(f"接口返回错误: {msg}", level="error")
                return None

            # 处理返回的数据
            page = json_data.get("data") or {}
            cursor = page.get("cursor") or ""
            has_more = bool(page.get("has_more")) and bool(cursor)

            for user in page.get("user_list") or []:
                nickname = user.get("nickname", "")
                # 跨页去重：同一 user_id 只处理一次
                user_id = user.get("user_id", "")
                if not user_id or user_id in seen_user_ids:
                    continue
                seen_user_ids.add(user_id)

                # live 块存在且 live_status == 2 才算正在直播
                live = user.get("live") or {}
                is_live = bool(live) and int(live.get("live_status") or 0) == 2
                info = {
                    "nickname": nickname,
                    "user_id": user_id,
                    "remark": user.get("remark") or "",
                    "avatar": _pick_value(user, "image", "avatar", "avatar_url"),
                    "desc": (user.get("desc") or "").split("\n")[0],
                    "fan_count": int(user.get("fan_count") or 0),
                    "is_live": is_live,
                    "room_id": "",
                    "room_start_time": "",
                    "title": "",
                    "quality": "",
                    "resolution": "",
                    "video_url": "",
                    "last_record": profiles.sync_user_profile(user_id, nickname),
                    "auto_record": profiles.is_auto_record(user_id),
                }
                if is_live:
                    stream = _parse_live_stream_info(live)
                    live_link = _pick_value(live, "live_link")
                    flv_url = ""
                    try:
                        flv_url = extract_flv_urls(live_link) if live_link else ""
                        if not live_link:
                            raise ValueError("接口没有给出 live_link")
                    except Exception as exc:
                        state.emit_log(f"[{nickname}] 取不到直播流地址：{exc}", level="warn")
                    info.update({
                        "room_id": str(_pick_value(live, "room_id")),
                        "room_start_time": (utils.transform_int_to_time(live["start_time"])
                                            if live.get("start_time") else ""),
                        "title": str(_pick_value(_room_extra(live), "live_title", "title") or ""),
                        "quality": stream.get("quality", "原画"),
                        "resolution": stream.get("resolution", ""),
                        "video_url": flv_url.replace(".flv", "_orig.flv") if flv_url else "",
                    })
                result.append(info)

            if has_more:
                # 翻页之间稍作停顿：几百位关注要翻十几页，一口气打满只会先撞上自家限流
                time.sleep(0.15)

        # 拉到了就算登录态恢复：红灯灭掉，下一次再失效时托盘还能重新弹一次
        state.notify_ui("auth", {"expired": False})
        return result

    except requests.exceptions.RequestException as e:
        state.emit_log(f"网络请求异常: {e}", level="warn")
        return None
    except Exception as e:
        state.emit_log(f"处理数据时发生错误: {e}", level="error")
        return None


def build_download_info(user: dict) -> dict:
    """从关注列表数据中提取录制所需字段（供 check_and_launch_download 使用）"""
    return {
        "nickname": user.get("nickname", ""),
        "user_id": user.get("user_id", ""),
        "room_id": user.get("room_id", ""),
        "room_start_time": user.get("room_start_time") or utils.current_time_formatted(),
        "video_url": user.get("video_url", ""),
        # 以下字段供文件命名规则的 {remarks} / {title} 使用
        "remark": user.get("remark", ""),
        "title": user.get("title", ""),
        "desc": user.get("desc", ""),
    }


def check_and_launch_download(user_info):
    """检查指定用户的直播状态并启动下载（在调用方线程里直接执行）"""
    download_key = f"{user_info['user_id']}_{user_info['room_id']}"
    # 原子地登记下载任务：已存在则跳过,避免重复下载(修复 TOCTOU 竞态)
    with state.active_downloads_lock:
        if download_key in state.active_downloads:
            return
        state.active_downloads.add(download_key)
    state.mark_launched(download_key)   # 同一场直播只开录一次，轮询不会反复补录
    recorder.download_flv_stream_with_pipe(user_info, download_key)
