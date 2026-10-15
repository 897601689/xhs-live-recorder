# 小红书直播录制器 v1.0

盯着关注列表：主播一开播就用 ffmpeg 把直播流录到本地。PySide6 界面 + 托盘常驻，
也可以完全不碰界面，用终端模式跑同一套主循环。

## 跑起来

```bash
pip install -r requirements.txt     # 终端模式其实只需要 requests
python main.py                      # 图形界面
python main.py --cli                # 纯终端轮询，不开界面
```

外部工具得靠你自己机器上有，程序不内置、也不去下载：

| 工具 | 干什么用 | 没找到会怎样 |
| --- | --- | --- |
| `ffmpeg` | 录制必需 | 录制不可用：标题栏那枚胶囊红着写「缺少 ffmpeg」，设置页那行也直说没找到 |
| `ffplay` | 只有「实时预览」用它 | 「···」菜单里预览那项是灰的，录制照旧 |

两者出自同一个包。查找顺序是 **PATH → 程序所在目录 → 已经找到的另一个的旁边**
（`ffplay` 常常和 `ffmpeg` 躺在同一个 `bin` 里，但那台机器未必把整个目录加进了 PATH）。
把 `ffmpeg.exe` 放在项目根目录（源码运行）或 exe 同目录（打包后）也认。启动日志里写明了
到底找到的哪一个、走的是哪条路径。这个查找只在启动时做一次，补完文件要**重启**才算数。

第一次打开界面，去 **设置 → 登录态** 粘贴自己的 `sid`，否则拉不到关注列表
（标题栏那枚胶囊会红着写「登录态失效」，托盘提醒一次）。整段 `xy-common-params` 或者请求 URL
直接粘进去也行，程序自己把 `sid` 抠出来。

## 它会做什么

- **关注列表**：卡片 / 列表两种视图；直播、录制、监听、平台四个筛选下拉，表头点着排序，
  列可开关，每页 10~100 或全部。
- **自动录制**：按主播单独的「自动录制」开关，**默认开**。同一场直播只自动开录一次，
  不会每轮轮询都补一遍；这轮没录成的，下一轮接着试。
- **断流续录**：网络抖动、ffmpeg 崩了、流被切，都会退避重连（最多 20 次）。续出来的段落进
  同一个目录，文件名带 `-2 / -3`，计时按整场算不会归零。
- **磁盘与封箱**：剩余低于 500MB 不开录，录制中低于 120MB 主动正常封箱。中断的半成品
  只要超过 1MB 就留着，只剩几 KB 空头才删。
- **实时预览**：从「···」菜单（或右键卡片）开，一路一个 ffplay 进程，窗口包成一枚 9:16 卡片
  压在主窗口之上。它是**第二条**打到同一直播间的连接，所以不自动开、也不断流自动续。
- **常驻与收尾**：点关闭只是把窗口藏进托盘（后台继续监控录制），真退出走托盘菜单的「退出」。
  一台机器只允许一个实例，锁是操作系统层面的文件区域锁，进程被强杀或系统重启都会随句柄回收。
- **日志落盘**：每条提示除了进界面的日志抽屉，同时写 `~/.xhs_live_recorder/recorder.log`
  （到 1MB 转一份旧的）。里面只记「登录态配没配」，绝不记它的值。

## 文件都在哪

| 位置 | 内容 |
| --- | --- |
| `~/.xhs_live_recorder/ui_settings.json` | 登录态 sid、主题、输出目录、轮询间隔、命名模板、分页与列显示 |
| `~/.xhs_live_recorder/user_profiles.json` | 每位主播的自动录制开关与最近录制时间（写入防抖 5 秒，退出前统一落盘） |
| `~/.xhs_live_recorder/recorder.log` | 运行日志，`.log.1` 是转过的那份 |
| `~/.xhs_live_recorder/app.lock` | 单实例锁，进程活着才存在 |
| `~/Downloads/小红书直播/` | 录出来的文件（默认根目录，设置里可换） |

登录态也可以用环境变量 `XHS_SID` 给，`ui_settings.json` 里的值优先。

## 文件名怎么拼

默认规则：`{platform}/{owner}/{owner}-{year}-{month}-{date} {hour}-{min}-{sec}`

可用标签：`{platform}` `{channelId}` `{remarks}` `{owner}` `{title}` `{year}` `{month}`
`{date}` `{hour}` `{min}` `{sec}` `{ms}`。规则里至少得有一个标签，空标签（比如没取到标题的
`{title}`）留下的多余分隔符会自动折叠掉。

## 代码在哪

```
main.py            入口：解析 --cli，决定开界面还是跑终端
xhs/               核心层，不 import Qt
  api.py           关注列表：翻页、跨页去重、登录态语义
  net.py           HTTP 通道：每线程连接池、公共请求头
  monitor.py       轮询主循环（终端与界面共用）
  recorder.py      一路录制：ffmpeg 子进程、断流续录、封箱与保留
  preview.py       ffplay 进程与窗口嵌入
  profiles.py      主播档案（自动录制开关 / 最近录制时间）
  settings.py      界面偏好读写、输出目录、命名模板校验
  instance.py      单实例锁
  state.py         录制任务状态、日志与界面回调桥接
  config.py        运行常量与路径
xhs/ui/            界面层：main_window / following / entries / widgets / chrome / theme …
tests/             三套测试（见下）
tools/make_icon.py 生成 app_icon.ico
recorder.spec      PyInstaller 配置
```

## 打包成 exe

```bash
pip install pyinstaller
python tools/make_icon.py              # 只在改了图标画法之后需要重跑
python -m PyInstaller recorder.spec    # 产物 dist/小红书直播录制器.exe
```

`app_icon.ico` 已经生成好并进了仓库，所以 exe 的文件图标、快捷方式和固定到任务栏的图块都有图。
打包出来是无控制台窗口（双击不弹黑框；ffmpeg / ffplay 子进程同样不带黑框），要用终端模式请拿
源码跑。ffmpeg 不打包进去，装的时候把它和 exe 放一起或加进 PATH 即可。

## 测试

```bash
python tests/static_check.py     # 静态：未定义名字、可疑的 self 属性读取
python tests/smoke_core.py       # 核心：断流续录 / 中断文件 / 档案防抖 / 关注列表主循环 / 锁
python tests/smoke_gui.py        # 界面：布局几何、菜单、下拉、预览卡片、退出流程
```

三个都不联网、不碰真实下载目录和用户档案（`smoke_gui.py` 自己会把 Qt 切成 offscreen，不需要
额外设环境变量）。核心层那份把 `net.fetch_page` 换成一串假页面来跑整条拉取主循环 —— 翻页、
跨页去重、登录态失效的两个分支、非 200，接口字段形状一变就会在这里先露馅。

## 三条边界

- **sid 等同账号凭证。** 它只写在 `~/.xhs_live_recorder/ui_settings.json` 或环境变量 `XHS_SID`
  里，不进源码、不进打包产物、不进日志。别把配置目录发给别人，也别提交进仓库。
- 拉取走的是 app 端接口（`edith.xiaohongshu.com` + `xy-common-params` 那套客户端参数），
  轮询默认在 30~90 秒之间随机（可设 5~3600 秒）。把它调更低只是让自己更快撞上风控，
  不会更快发现开播。
- 这个程序只做「把正在直播的流录到本地」这一件事，不碰加密流、不做批量抓取或规避检测。

## 出问题先看哪

按顺序看 `~/.xhs_live_recorder/recorder.log`：开头那行启动横幅写明了模式、Python 版本、
ffmpeg / ffplay 到底找到没有、输出目录、配置目录、登录态配没配 —— 这几项能解释绝大多数
「什么都没录到」。

## 最后

本程序只适用于学习交流 —— 用的是你自己的登录态，录的也是你自己关注列表里的直播。
