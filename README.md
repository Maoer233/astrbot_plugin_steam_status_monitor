# Steam 状态监控插件V3 ![MIT](https://img.shields.io/badge/LICENSE-MIT-blue?style=flat-square) ![Python](https://img.shields.io/badge/Python-3.7+-blue?style=flat-square) ![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.24.2-purple?style=flat-square) [![Stars](https://img.shields.io/github/stars/Maoer233/astrbot_plugin_steam_status_monitor?style=flat-square)](https://github.com/Maoer233/astrbot_plugin_steam_status_monitor) [![Last Commit](https://img.shields.io/github/last-commit/Maoer233/astrbot_plugin_steam_status_monitor?style=flat-square)](https://github.com/Maoer233/astrbot_plugin_steam_status_monitor) [![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen?style=flat-square)](https://github.com/Maoer233/astrbot_plugin_steam_status_monitor/pulls)

## 访问统计
![访问统计](https://count.getloli.com/get/@astrbot_ssm?theme=rule34)

本插件是专为AstrBot设计的插件，用于定时轮询 Steam Web API，监控指定玩家的在线/离线/游戏状态变更，并在状态变化时推送通知。支持多 SteamID 监控，自动记录游玩日志，支持群聊分组，数据持久化，支持丰富指令。

## 本次修复

### 多游戏显示问题

- 修复排行榜中同一玩家只显示一款游戏的问题：统计周期内游玩的多款游戏现在会分别展示，不再相互覆盖。
- 排名仍按玩家总时长计算，同时保留每款游戏的独立时长；排行榜卡片会根据游戏数量调整布局，避免游戏名称和时长被截断。
- 本群排行、全群排行以及每日自动推送共用相同的多游戏统计结构，保证各入口显示口径一致。

### 时长累计错误

- 修复同一个 SteamID 被多个群同时监控时，同一局游戏会按群重复写入 `session_records`、导致总时长成倍增加的问题。
- Session 使用业务日期、开始时间和游戏 ID 生成稳定的 `session_id`，写入前按 `session_id` 去重，因此同一局游戏只累计一次。
- Session 时长保留两位小数，避免短时游玩被整数分钟截断；兼容数据 `play_records` 仍沿用整数分钟格式。
- 修复网络波动恢复时状态字段判断错误的问题，避免网络抖动被误判为新一局游戏，并防止后续产生错误退出通知或重复结算。

> 新的去重逻辑只阻止后续产生重复 Session，不会自动清理已经写入的历史重复记录。历史数据需要备份后单独修复，并同步校正或重建 `play_records`。

本次模块拆分的目的、边界、兼容方式和影响范围见 [REFACTORING.md](REFACTORING.md)。

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
- **原生逐指令权限**：每条指令使用 AstrBot 框架的 `admin/member` 权限，不再维护插件内部权限等级
- **AstrBot 内置管理页**：仪表盘、群聊、绑定、每日推送和权限管理直接集成在 AstrBot WebUI，无需额外端口
- **权限配置同步**：内置管理页的“指令权限”直接读写 AstrBot 框架配置，并同步当前运行时权限

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
5. 如需使用管理页面，在 AstrBot WebUI 的插件详情中打开“Steam 状态监控”页面。

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
| `enable_steam_style` | Steam列表渲染风格开关（开启=steam风格；关闭=原卡片风格） | true |
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
![开始游戏示例](https://raw.githubusercontent.com/Maoer233/astrbot_plugin_steam_status_monitor/main/assets/images/str.png)
![结束游戏示例](https://raw.githubusercontent.com/Maoer233/astrbot_plugin_steam_status_monitor/main/assets/images/stop.png)
![成就推送示例](https://raw.githubusercontent.com/Maoer233/astrbot_plugin_steam_status_monitor/main/assets/images/achievement.png)
![WebUI 管理后台](https://raw.githubusercontent.com/Maoer233/astrbot_plugin_steam_status_monitor/main/assets/images/webui.png)
![List 玩家列表](https://raw.githubusercontent.com/Maoer233/astrbot_plugin_steam_status_monitor/main/assets/images/list.png)


## QQ 官方机器人后台配置面板

### 功能用途

插件 WebUI 左侧的 **QQ 官方机器人** 页面用于集中维护 QQ 开放平台接入参数和 QQ 指令面板参数，支持读取、修改、保存及恢复默认配置。后端会再次校验所有输入，机器人密钥在页面读取时自动脱敏，不会以明文回显。

> 该页面保存的是本插件使用的 QQ 官方机器人参数，不会自动修改 AstrBot“机器人”页面中的适配器配置。开启“后台 QQ 官方机器人配置”后，插件同步 QQ 指令面板时优先使用这里保存的 AppID 和密钥；关闭后继续使用当前 QQ 官方适配器的凭据。

### 配置项说明

| 配置项 | 说明 | 格式要求 |
| --- | --- | --- |
| 启用状态 | 是否让插件使用本面板中的 QQ 官方机器人凭据 | 开启时 AppID、密钥必填 |
| 机器人 AppID | QQ 开放平台分配的机器人 AppID | 5～20 位数字 |
| 机器人密钥 | QQ 开放平台分配的机器人 Secret | 8～256 个不含空白的字符；读取时脱敏 |
| 回调地址 | Webhook 接入使用的公网回调 URL | 完整的 `http://` 或 `https://` URL，不得包含用户名或密码 |
| 消息格式 | QQ 消息的目标格式 | `plain`（纯文本）或 `markdown` |
| 启用 QQ 指令面板 | 是否允许执行 `/steam qq菜单同步` | 布尔开关 |
| 使用场景 | 指令面板生效的会话类型 | `group`（群聊）或 `c2c`（单聊） |
| 目标群 OpenID | 指令面板关联的 QQ 官方群标识 | 每行一个或逗号分隔；不能填写普通 QQ 群号 |
| 群指令菜单项 | QQ 客户端菜单中显示的指令名称、说明和顺序 | 最多 20 项；指令必须以 `/` 开头且不能重复 |

### 自定义群指令菜单

后台的 **群指令菜单项** 模块用于决定 QQ 官方机器人菜单中展示哪些指令。每个菜单项由“指令”和“说明”组成，支持添加、删除以及通过上下按钮调整显示顺序；删除全部菜单项后，同步结果中不会展示指令。

该模块只维护 QQ 客户端中的菜单入口，不会自动注册或实现新的机器人指令。添加的指令必须已由本插件或 AstrBot 中的其他插件提供，否则用户点击后机器人无法处理。修改菜单后需要先保存配置，再执行 `/steam qq菜单同步` 将最新内容提交到 QQ 开放平台。

### 使用步骤

1. 打开插件 WebUI，进入左侧 **QQ 官方机器人**。
2. 填写 AppID、密钥；Webhook 模式再填写回调地址。
3. 选择消息格式、指令面板场景并按需填写目标群 OpenID。
4. 在 **群指令菜单项** 中添加、删除或排序需要展示的指令，并填写对应说明。
5. 开启相应状态后点击 **保存配置**。输入不合法时页面会显示具体错误且不会保存。
6. 重载插件，然后在目标 QQ 官方群中执行 `/steam qq菜单同步`；目标群 OpenID 留空时，插件会从当前官方群事件自动获取。
7. 需要清除本面板配置时点击 **恢复默认**；确认后会清空 AppID、密钥、回调地址、目标群及已保存的面板 ID，并恢复默认菜单项。

### 注意事项

- AppID 和密钥属于敏感凭据，请勿截图、提交到 Git 或发送给他人。
- 页面显示的 `******xxxx` 是脱敏密钥；不修改该字段直接保存时，原密钥会被保留。
- 回调地址只负责配置记录与格式校验，实际 Webhook 可达性、签名验证和 QQ 开放平台登记仍需在对应适配器及开放平台完成。
- `markdown` 是否可发送取决于机器人账号当前拥有的 QQ 开放平台能力。
- 普通 QQ 群号不能转换为群 OpenID。若不确定，请留空并从目标 QQ 官方群执行同步命令。
- 修改 AppID、密钥或启用状态后建议重载插件；修改指令面板参数后重新执行同步命令。

## 指令列表
- `/steam on` 启动本群Steam状态监控
- `/steam off` 停止本群Steam状态监控
- `/steam list` 列出本群所有玩家当前状态
- `/steam alllist [img|text]` 列出所有群聊玩家状态（默认图片，`text` 纯文本输出）
- `/steam config` 查看当前插件配置
- `/steam set [参数] [值]` 设置配置参数（如 `/steam set poll_interval_sec 30`）
- `/steam addid [SteamID/链接/好友码] [@用户] [备注名]` 添加玩家并可选 **@用户** 绑定（通过 @ 群成员完成QQ绑定，支持多种格式）
- `/steam delid [SteamID/好友码/链接]` 从本群监控列表删除SteamID
- `/steam push_group [SteamID]` 添加id到联动推送的副群（轮询一次通知多个群聊）
- `/steam delpush_group [SteamID]` 删除id联动推送的副群
- `/steam openbox [SteamID/好友码/链接]` 查看指定SteamID的全部详细信息
- `/steamwho @用户` / `/在干嘛 @用户`  即时查询 @绑定玩家的 Steam 状态
- `/steam rank [天数]` 查看本群游戏时长排行榜（默认今日，可指定天数）
- `/steam allrank [天数]` 查看所有群游戏时长排行榜（默认今日，可指定天数）
- `/steam rank_on [all|list|test|del]` 管理每日排行榜推送（默认每群独立排行；all=显式使用共享全局排行，list=查看状态，test=即刻推送，del [群号]=删除指定群推送）
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
- AstrBot >= 4.24.2

### 依赖安装方法
如果显示缺少依赖，你可以尝试下载以下工具来进行修复
pip install httpx pillow

可以添加QQ：1912584909 或加交流群：881855879 来反馈功能和建议 闲聊也欢迎喵~

## 🔗 关联项目
📢 **Steam Monitor 独立版**：[@NeP](https://github.com/nep-0) 用 Go 重写的零依赖版本，无需 AstrBot，单文件运行，Web 管理界面，开箱即用。
→ [github.com/nep-0/steam-monitor](https://github.com/nep-0/steam-monitor)

## ⭐ Stars

> 如果本项目对您的生活 / 工作产生了帮助，或者您关注本项目的未来发展，请给项目 Star，这是我维护这个开源项目的动力 ❤️。

## 更新记录
- 2026/08/29 开发更新
  - **版本修复**：发布 V4.2.0，修复 `/steam addid` 对已在监控中的 SteamID 无法补充备注或绑定的问题；现在支持通过 `@用户 备注` 或仅备注更新已有玩家信息。
  - **架构重构**：完成监控模块模块化拆分，分离监控管理、通知追踪、状态变更和分发路由职责，补充后台管理接口、统计逻辑及单元测试，降低主插件复杂度并改善跨群处理一致性。
  - **功能新增**：接入 ITAD 价格查询，完善 Steam 游戏详情卡片，增加评价、商店、地区和价格信息展示，并支持 CN、RU 区域价格摘要及对应币种渲染。
  - **通知修复**：按目标会话、Steam 用户、游戏、事件类型和事件时间进行幂等去重，避免同一状态事件经多个来源群路由后重复通知。
  - **交互修复**：移除不兼容的 `steam_first` 参数；候选游戏序号监听覆盖群聊和私聊，按会话隔离候选缓存，命中后阻止消息继续进入 LLM 处理。
  - **渲染修复**：统一价格查询与游戏详情渲染接口，修复传入 `region_prices` 时价格详情卡片渲染失败的问题。

- V4.2.0（2026/08/28）
  - **Bug 修复**：修复 `/steam addid` 添加已在监控中的 SteamID 时无法补充备注/绑定的问题；现在可对已监控玩家更新备注（支持 `@用户 备注` 及仅备注两种方式），列表/排行显示名同步生效

- V4.1.0（2026/08/28）
  - **功能新增**：支持设置反向代理（#38，感谢 @nep-0），新增 `steam_api_base` / `steam_store_base` / `sgdb_api_base` 三个代理地址配置项，可分别代理 Steam Web API、Steam 商店与 SteamGridDB，留空使用官方地址

- V4.0.0（2026/08/28）
  - **重大重构**：核心逻辑深度模块化拆分（#37，感谢 @OLRainM），steam_status_monitor.py 由单体拆分为 src/application/services/ 职责模块（监控/轮询/状态变更/成就/通知/QQ 菜单管理），新增 src/domain/monitoring/ 领域层（polling/state/transitions）；新增单元测试（分发列表路由回归、通知虚构 SteamID）
  - **Bug 修复**：防止跨群重复监控同一 SteamID 并自动转为推送群；完善分发路由与群组清理，避免主群与联动群重复投递；修复长玩家名称图片布局

- V3.4.0（2026/08/24）
  - **重大重构**：插件主体拆分为 src/ 分层结构（application/domain/infrastructure/presentation/shared），根入口 main.py 保持兼容；新增 QQ 官方机器人适配与后台指令面板（qq_official_* / qq_menu_*）；WebUI 管理接口性能优化（TTL 缓存/同键并发合并/缓存失效）
  - **Bug 修复**：修复初始化时跨群同一 SteamID 状态基线不一致；成就 API 地址改为可配置端点（steam_api_base）
  - **功能新增**：新增 Steam 官方 library_capsule_2x 高清竖版封面获取（SGDB 兜底）；初始化轮询改为批量查询 + status_override，减少重复 API 调用
  - **功能新增**：/steam list、/steam alllist、/steamwho 新增 Steam 好友列表风格渲染（enable_steam_style，默认关闭保持 V3.3.3 原卡片风格）
  - **功能优化**：重启后跳过插件停止期间遗留变化的陈旧播报（基于 states.json 写入时间判断，阈值 60 分钟）；logo 归位插件根目录

- V3.3.3（2026/07/30）
  - **功能新增**：新增网络波动通知开关（enable_network_fluctuation_notify），可单独关闭网络波动文本提醒
  - **功能新增**：开始游戏渲染图片右下角添加版本号水印（淡色小字）

- V3.3.2（2026/07/29）
  - **Bug 修复**：成就渲染 total_height 为浮点数导致 TypeError；修复为强制 int 转换

- V3.3.1（2026/07/28）
  - **Bug 修复**：WebUI 群聊管理添加玩家时，好友码和链接被拒绝；改用 resolve_steam_input 统一解析

- V3.3.0（2026/07/28）
  - **Bug 修复**：免费游戏（如 Apex Legends）开始游戏通知中游玩时长显示"缺省"，修复 GetOwnedGames API 缺少 include_played_free_games 参数
  - **文档优化**：优化 README 绑定机制说明，明确 @用户 绑定方式，修正命令前缀描述

- V3.2.7（2026/07/24）
  - **群聊管理聚合**：群详情表格增加绑定(@)/备注列，添加SteamID时支持同时绑定QQ
  - **批量导入**：新增"批量导入"按钮，支持空格分隔格式（SteamID/链接 @用户 备注），每行一条

- V3.2.6（2026/07/24）
  - **Bug 修复**：甘特图数据源重复叠加导致出现幽灵时间段（session_records 和 play_records 同时存在时重复渲染）

- V3.2.5（2026/07/24）
  - **通知开关优化**：新增 `enable_game_start_notify` 配置项，关闭后不发送开始游戏通知但仍记录时长
  - **addid 自动启用监控**：`/steam addid` 后自动启动监控，无需额外 `/steam on`
  - **WebUI 自动投递**：WebUI 添加的群首次收到消息时自动补全通知目标

- V3.2.4（2026/07/24）
  - **内置 WebUI**：外置 aiohttp 管理站迁移为 AstrBot Plugin Pages，复用 Dashboard 登录鉴权，不再监听独立端口
  - **分群每日榜单**：默认按每个目标群的监控成员分别聚合、渲染和推送，单群无记录或失败不影响其他群
  - **推送范围语义**：管理页明确区分"接收群聊"和"榜单内容范围"，全局榜单仅在显式选择时启用

- V3.2.3（2026/07/23）
  - **权限系统迁移 (by LitChi-bit)**：回退 AstrBot 原生逐指令 `admin/member` 权限，移除插件内部 `permission_level`
  - **WebUI 权限管理 (by LitChi-bit)**：新增"指令权限"逐条配置，直接同步 AstrBot 框架持久化配置与运行时权限
  - **权限提示优化**：框架权限不足提示改为 WebUI 操作引导

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
  - **自定义备注名**：所有推送通知、list、rank、alllist、/在干嘛 图片优先显示备注
  - **新增指令**：/steamwho @用户 / /在干嘛 @用户 即时查询单人 Steam 状态
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

## 贡献者

感谢以下社区贡献者（V3.4.0）：

- [@OLRainM](https://github.com/OLRainM)：模块化重构、QQ 官方机器人适配、WebUI 管理接口性能优化
- [@e-legy](https://github.com/e-legy)：跨群状态基线修复、Steam 官方高清竖版封面获取
- [@guairenwei](https://github.com/guairenwei)：Steam 好友列表风格渲染
