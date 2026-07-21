# Steam 状态监控插件V3

## 访问统计
![访问统计](https://count.getloli.com/get/@astrbot_ssm?theme=rule34)

本插件是专为AstrBot设计的插件，用于定时轮询 Steam Web API，监控指定玩家的在线/离线/游戏状态变更，并在状态变化时推送通知。支持多 SteamID 监控，自动记录游玩日志，支持群聊分组，数据持久化，支持丰富指令。

## 功能特性
- 支持定时轮询多个 SteamID 的状态，分群管理，每个群聊可独立配置监控玩家
- 检测玩家上线、下线、开始/切换/退出游戏等状态变更，自动推送游戏启动/关闭提醒
- 成就变动自动推送提醒
- **头像框渲染**：开始游戏/结束游戏/list/rank 均支持 Steam 头像框，本地优先缓存 7 天
- **游戏时长排行榜**：支持  / ，按数字天数查询，凌晨 4:00 天分界
- 游戏时长排行榜：支持  本群排行和  所有群排行，可按数字天数查询
- 智能轮询 + 固定轮询双模式可切换，默认为1-30分钟查询一次状态，取决于steam的上次在线时间
- 持久化记录玩家游玩日志，重启bot后状态不会丢失
- **批量查询优化**：采用 Steam 官方批量接口（单次最多 100 个 ID），大幅降低 API 调用次数，从根本上避免触发 Steam 限流（HTTP 429 / x-eresult: 84）
- **多种 ID 输入格式**：`addid` 现支持 SteamID64、个人资料链接、自定义 vanity URL、`s.team` 短链、8 位好友码等多种格式
- **通知开关精细化**：可独立控制游戏结束通知、成就推送、以及图片/文本推送方式
- **网络代理支持**：可配置 http / https / socks5 代理，改善网络环境下的数据获取稳定性
- **字体自动管理**：自动检测并加载插件 `fonts` 目录下的 NotoSansHans 系列字体，渲染更稳定
- **性能优化**：节流写盘、单点异常隔离、批量预拉取，避免拖慢 AstrBot 主进程与 WebUI

## 默认轮询间隔说明（智能轮询模式）
| 玩家最近在线时间      | 轮询间隔 |
|----------------------|---------|
| 游戏中               | 1分钟   |
| 12分钟内             | 3分钟   |
| 12分钟~3小时         | 5分钟   |
| 3小时~24小时         | 10分钟  |
| 24~48小时            | 20分钟  |
| 超过48小时           | 30分钟  |

## 快速上手
1. 在AstrBot网页后台的配置中配置 Steam_Web_API_Key：[点击获取](https://steamcommunity.com/dev/apikey)
2. 在AstrBot网页后台的配置中配置 SGDB_API_KEY（用于获取封面图，可选）：[点击获取](https://www.steamgriddb.com/profile/preferences/api)
3. 在需要进行提醒的群聊输入指令添加要监控的玩家（以下格式均支持）：
   - `/steam addid 7656119xxxxxxxxx`（SteamID64）
   - `/steam addid https://steamcommunity.com/profiles/7656119xxxxxxxxx`（个人资料链接）
   - `/steam addid https://steamcommunity.com/id/customname`（自定义 vanity URL）
   - `/steam addid https://s.team/p/7656119xxxxxxxxx`（s.team 短链）
   - `/steam addid 123456789`（8 位好友码）
4. 启动轮询：
   `/steam on`  启动本群 Steam 状态监控，后续状态变更会自动推送。

## 配置项说明
| 配置项 | 说明 | 默认值 |
|-------|------|-------|
| `steam_api_key` | Steam Web API Key | — |
| `sgdb_api_key` | SteamGridDB API Key（用于封面图） | — |
| `fixed_poll_interval` | 固定轮询间隔（秒），为 0 时使用智能轮询 | 0 |
| `smart_poll_intervals` | 智能轮询各状态间隔（分钟，逗号分隔） | `1,3,5,10,20,30` |
| `retry_times` | Steam API 请求重试次数 | 3 |
| `max_group_size` | 单群最大监控人数 | 20 |
| `detailed_poll_log` | 详细轮询日志开关 | true |
| `enable_achievement_poll` | 成就轮询推送开关 | true |
| `enable_game_end_notify` | 游戏结束通知开关 | true |
| `notify_send_image` | 通知发送图片开关 | true |
| `notify_send_text` | 通知发送文本开关 | true |
| `enable_proxy` | 启用网络代理 | false |
| `proxy_url` | 代理链接（如 `http://127.0.0.1:7890`） | 空 |

> 带「修改后重启AstrBot生效」标注的配置项需重启后生效。

## 注意事项
- 获取速度与是否成功获取 Steam 数据取决于网络环境。建议通过加速或代理（现已内置代理配置项）来保证稳定的查询状态。
- 如果出现未知的轮询错误可以使用 `/steam clear_allids` 来清除所有群聊的轮询 id。
- 修改插件参数后，如果出现重复通知的情况，请不要重载插件，而是重启 AstrBot。
- 如果出现未知的无法提醒，但轮询显示正常的情况，请使用 `/steam on/off` 进行修复。
- 监控人数较多时，建议适当调高 `max_group_size` 并保持智能轮询，以兼顾时效与 Steam 限流。

## 演示截图
![开始游戏示例](https://raw.githubusercontent.com/Maoer233/astrbot_plugin_steam_status_monitor/main/str.png)
![结束游戏示例](https://raw.githubusercontent.com/Maoer233/astrbot_plugin_steam_status_monitor/main/stop.png)
![成就推送示例](https://raw.githubusercontent.com/Maoer233/astrbot_plugin_steam_status_monitor/main/achievement.png)
![WebUI 管理后台](https://raw.githubusercontent.com/Maoer233/astrbot_plugin_steam_status_monitor/main/webui.png)
![List 玩家列表](https://raw.githubusercontent.com/Maoer233/astrbot_plugin_steam_status_monitor/main/list.png)


## 指令列表
- `/steam on` 启动本群Steam状态监控
- `/steam off` 停止本群Steam状态监控
- `/steam list` 列出本群所有玩家当前状态
- `/steam alllist [img|text]` 列出所有群聊玩家状态（默认图片，`text` 纯文本输出）
- `/steam config` 查看当前插件配置
- `/steam set [参数] [值]` 设置配置参数（如 `/steam set poll_interval_sec 30`）
- `/steam addid [SteamID/链接/好友码] [@用户] [备注名]` 添加玩家并可选绑定QQ（支持多种格式）
- `/steam delid [SteamID/好友码/链接]` 从本群监控列表删除SteamID
- `/steam push_group [SteamID]` 添加id到联动推送的副群（轮询一次通知多个群聊）
- `/steam delpush_group [SteamID]` 删除id联动推送的副群
- `/steam openbox [SteamID/好友码/链接]` 查看指定SteamID的全部详细信息
- `.steamwho @用户` / `.在干嘛 @用户`  即时查询QQ绑定玩家的Steam状态
- `/steam rank [天数]` 查看本群游戏时长排行榜（默认今日，可指定天数）
- `/steam allrank [天数]` 查看所有群游戏时长排行榜（默认今日，可指定天数）
- `/steam rank_on [all|list|test|del]` 管理每日排行榜推送（all=全局排行，list=查看状态，test=即刻推送，del [群号]=删除指定群推送）
- `/steam rs` 清除所有状态并初始化
- `/steam achievement_on` 开启本群Steam成就推送
- `/steam achievement_off` 关闭本群Steam成就推送
- `/steam test_achievement_render [steamid] [gameid] [数量]` 测试成就图片渲染
- `/steam test_game_start_render [steamid] [gameid]` 测试开始游戏图片渲染
- `/steam清除缓存` 清除所有头像、封面图等图片缓存
- `/steam help` 显示所有指令帮助

## 依赖
- Python 3.7+
- httpx
- Pillow
- AstrBot 框架

### 依赖安装方法
如果显示缺少依赖，你可以尝试下载以下工具来进行修复
pip install httpx pillow

可以添加QQ：1912584909 来反馈功能和建议 闲聊也欢迎喵~

## ⭐ Stars

> 如果本项目对您的生活 / 工作产生了帮助，或者您关注本项目的未来发展，请给项目 Star，这是我维护这个开源项目的动力 ❤️。

## 更新记录
- V3.2.1（2026/07/16）
  - **WebUI 管理后台（Beta）**：基于 aiohttp 的嵌入式管理页面，支持仪表盘、甘特图、热力图、群聊管理、绑定管理、每日推送设置
  - **仪表盘**：展示监控统计、玩家排行榜、热门游戏饼图、在线玩家卡片（状态色标识、封面提色）
  - **甘特图**：展示游戏时间窗口，支持今天/昨天/7天/30天切换
  - **热力图**：团队贡献日历（GitHub 风格）和个人详情页，含游戏占比分析
  - **群聊管理**：增删群聊、增删 SteamID、状态展示
  - **连接测试**：一键测试 Steam API / Steam Store / SGDB 连通性
  - **Gantt 数据源优化**：优先使用 session_records 真实时间戳，回退按 1:1 分钟映射
  - **新增依赖**：`aiohttp>=3.9.0`（Web 服务器）

- V3.1.16（2026/07/15）
  - **Bug 修复**：修复旧版 start_play_times 数据格式不兼容导致轮询崩溃（int → dict 自动迁移）

- V3.1.15（2026/07/13）
  - **功能改进**：alllist 支持 img/text 双模式输出，卡片叠加状态色渐变，修复 personastate 状态识别（区分在线/忙碌/离开/打盹），头像框默认缓存 30 天

- V3.1.14（2026/07/13）
  - **功能改进**：统一权限系统，移除 AstrBot 框架层 ADMIN 权限装饰器，所有指令改用插件内部 permission_level 控制

- V3.1.13（2026/07/09）
  - **Bug 修复**：定时排行榜推送在主轮询无玩家到点时被跳过，导致推送失效

- V3.1.12（2026/07/08）
  - **QQ-SteamID 绑定系统**：addid 支持 @用户 [备注名]，绑定即监控
  - **自定义备注名**：所有推送通知、list、rank、alllist、.在干嘛 图片优先显示备注
  - **新增指令**：.steamwho @用户 / .在干嘛 @用户 即时查询单人 Steam 状态
  - **delid/openbox 支持多格式**：好友码、链接均可

- V3.1.11（2026/07/07）
  - **封面降级优化**：竖版封面缺失时叠加横版 header_image，永久缓存
  - **排行榜视觉优化**：进度条改为 Top1 满格基准，显示百分比对比，总时长金色
  - **游戏过滤**：黑白名单模式（全部/白名单/黑名单），按 gameid 过滤

- V3.1.10（2026/07/06）
  - **Bug 修复**：修复 WebUI 保存配置时 smart_poll_intervals 类型校验失败（list vs string），init 阶段强制归一化为逗号分隔字符串
  - **代理增强**：SOCKS5 代理自动安装 socksio 依赖（pip install httpx[socks]），安装失败则打印清晰指引
  - **代理增强**：fetch_player_status / fetch_player_statuses_batch 异常处理加固，try 包裹 async with httpx.AsyncClient，防止 context manager 异常穿透到主轮询
  - **依赖更新**：requirements.txt httpx → httpx[socks]

- V3.1.9（2026/07/06）
  - **Bug 修复**：Steam API 返回非 dict 错误响应（如 x-eresult: 84）时不再崩溃，改为优雅降级并输出诊断日志
  - **Bug 修复**：addid 分隔符从 [,.\s] 改为仅中英文逗号，避免 URL 中的 . 被错误截断
  - **Bug 修复**：ResolveVanityURL 同样加 isinstance 守卫，防止异常响应导致崩溃
  - **指令优化**：README 更新 rank_on 统一用法，移除已废弃的 rank_off

- V3.1.8（2026/07/05）
  - **指令增强**：/steam delid 支持跨群删除（私聊传群号），退群也能清理监控

- V3.1.7（2026/07/05）
  - **Bug 修复**：重启插件后不再重复播报开始/结束游戏通知（初始化静默建立状态基线）
  - **Bug 修复**：移除持久化加载时错误的 gameid 清除逻辑，消除重启误判

- V3.1.6（2026/07/05）
  - **性能优化**：主轮询跨群合并批量查询，N个群从N次API调用降为1次（自动去重）

- V3.1.5（2026/07/05）
  - **Bug 修复**：定时排行榜推送 (rank_on / rank_on all) 目标群为空导致无推送
  - **新增配置**：排行榜推送时间可自定义（rank_push_hour / rank_push_minute，默认 8:30）
  - **指令优化**：/steam rank_on 整合 list（查看状态）/ test（即刻推送）/ del（删除推送）

- V3.1.4（2026/07/05）
  - **性能优化**：steam_list / steam_alllist / steam_on 初始化全部改用批量查询接口，大幅减少 API 调用次数

- V3.1.2（2026/07/04）
  - **Bug 修复**：排行榜 (rank/allrank) 封面获取日期键与数据聚合对齐，修复封面不显示
  - **Bug 修复**：排行榜 (rank/allrank) 新增 Steam 头像框渲染
  - **Bug 修复**：玩家切换游戏时，上一款游戏游玩时长不再丢失

- V3.1.1（2026/07/04）
  - **新增头像框显示**：开始游戏/结束游戏/list/rank 图片均支持显示 Steam 头像框
  - **缓存配置化**：头像/头像框/封面缓存时间可在 WebUI 配置，默认头像1天/头像框7天/封面永不
  - **alllist图片渲染**： steam alllist 改为图片渲染
  - **权限分级**：新增 permission_level 配置（1=管理员限定 2=查询指令放开 3=开关+添加ID放开）


- V3.1.0（2026/07/04）
  - **排行榜功能**：新增游戏时长排行榜，支持 `steam rank` 本群排行和 `steam allrank` 所有群排行
  - 参数由 week/month 改为任意数字天数（如 `steam rank 15`），默认返回当天
  - 每天凌晨 4:00 为天分界点，定时播报默认早上 8:30 推送昨日排行榜
  - `steam rank_on` / `steam rank_off` 开启/关闭每群排行榜自动推送
  - 修复重启插件后已通知过的退出记录重复推送的问题

- V3.0.0（2026/07/03）重大更新
  - **性能与稳定性大幅优化**：采用 Steam 官方批量查询接口（单次最多 100 个 ID），大幅降低 API 调用次数，从根本上避免触发 Steam 限流（HTTP 429 / x-eresult: 84）及 IP 被封禁；批量失败时自动降级为单查，保证可用性
  - **轮询架构重构**：重写全局轮询循环，按动态到点查询 + 异常隔离（`return_exceptions=True`），修复在线玩家不再轮询、离线玩家轮询间隔越来越长的问题
  - **WebUI 卡顿修复**：引入持久化数据脏标志 + 节流写盘（默认 300 秒一次），避免高频写盘拖慢 AstrBot 主进程与 WebUI
  - **退出推送修复**：新增延迟退出检查与去重机制（`_pending_quit_tasks`），修复同一玩家同一游戏在短时间内重复触发退出通知的问题；优化推送会话管理，修复 `未设置推送会话，无法发送消息` 错误
  - **多种 ID 输入格式**：`addid` 现支持 SteamID64、个人资料链接、自定义 vanity URL（自动调用 ResolveVanityURL 解析）、`s.team` 短链、8 位好友码
  - **通知开关精细化**：新增 `enable_game_end_notify`（可单独关闭游戏结束通知）、`notify_send_image` / `notify_send_text`（图片/文本推送可独立控制）
  - **配置项开放**：`max_group_size`（单群最大监控人数）由硬编码改为可配置项，方便大群 / 粉丝群使用
  - **网络代理支持**：新增 `enable_proxy` / `proxy_url` 配置项，支持 http / https / socks5 代理（来自社区 PR）
  - **字体自动管理**：启动时自动检测并加载插件 `fonts` 目录下的 NotoSansHans 系列字体，缓存到数据目录，渲染更稳定
  - **成就系统优化**：新增 `enable_achievement_poll` 开关，获取成就失败的游戏自动加入黑名单跳过轮询
  - **游戏名中文化**：优先通过 Steam 商店 API 获取游戏中文名，无则回退英文名
- V2.2.0
  添加了缺失的封面的图片显示
  添加了新功能，可以将已经轮询中账号，联动推送到多个副群（适用于多个粉丝群的情况）
