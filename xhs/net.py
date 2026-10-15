"""HTTP 通道：每线程连接池、公共请求头与接口地址"""

import requests
import threading
import time
from requests.adapters import HTTPAdapter

from xhs import settings, state


_thread_local = threading.local()   # 见 _session()：每个线程一份连接池


def _session() -> requests.Session:
    """按线程独享 Session：同域名复用 keep-alive 连接，省掉每页一次 TLS 握手

    轮询会持续翻页拉取关注列表、线程池会并发下载头像，都用同一个域名；
    又不希望多个线程共用同一个连接池（requests 的 Session 并非严格线程安全），
    因此每个线程各持一份，长驻线程（轮询 / 图片池）能吃到复用收益。
    """
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        adapter = HTTPAdapter(pool_connections=8, pool_maxsize=16)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        _thread_local.session = session
    return session


# 提取公共headers部分
common_headers = {
    "xy-direction": "94",
    "xy-scene": "point=1185&fs=0",
    "xy-platform-info": "platform=iOS&version=9.46&build=9460832&bundle=com.xingin.discover",
}


# xy-common-params 模板：{t} 替换成当前时间戳，{sid} 替换成当前登录态
# 逐项消融实测（2026-10-14，真账号翻页对照）：该接口只校验 sid，其余字段删掉都照样返回数据；
# shield、id_token 早前已验证不参与校验并移除，本轮再删 did / gid —— 设备标识，属于「换台机器
# 就不该跟着走」的那一类，删掉后 25 项参数仍正常返回关注列表。
# 剩下的都是版本 / 环境类静态描述（app_id、build、platform…），不指向具体设备，保留以维持
# 正常客户端特征
common_params_template = (
    "SUE=1&active_ctry=CN&app_id=ECFAAF02&auto_trans=0&build=9460832&channel=AppStore&data_ctry=CN"
    "&dlang=zh&fid="
    "&holder_ctry=CN"
    "&identifier_flag=0&is_mac=0&lang=zh-Hans&launch_id=810704618&mlanguage=zh_cn&nqe_score=92"
    "&overseas_channel=0&platform=iOS&project_id=ECFAAF"
    "&sid={sid}"
    "&t={t}&teenager=0&tz=Asia/Shanghai&uis=light&version=9.46"
)


def build_headers() -> dict:
    """组装请求头：t 取当前时间戳，sid 取当前登录态（实测只校验 sid）"""
    headers = common_headers.copy()
    headers["xy-common-params"] = common_params_template.format(
        t=int(time.time()), sid=settings.current_sid())
    return headers

# 提取相同的URL
base_url = "https://edith.xiaohongshu.com/api/sns/v2/user/followings/self"

# 一次请求的超时上限（连接 5 秒 / 读取 15 秒）：不给上限时，网络半死的情况下 requests
# 会一直挂着，轮询线程再也回不来，而界面那盏「监控中」还亮着 —— 最坏的静默故障。
HTTP_TIMEOUT = (5, 15)


def fetch_page(cursor: str = "") -> dict:
    """拉关注列表的一页，原样返回解析好的 JSON dict；非 200 返回 None（原因已写日志）

    这里只管「发一次、拿回一份 dict」：翻页、去重、登录态语义都留在 api 层。
    分开之后测试喂一串假页面就能覆盖整条主循环，不用伪造 Session。
    """
    response = _session().get(base_url, headers=build_headers(),
                              params={"cursor": cursor, "order": "2"},
                              timeout=HTTP_TIMEOUT)
    if response.status_code != 200:
        state.emit_log(f"请求失败，状态码: {response.status_code}", level="error")
        return None
    return response.json()
