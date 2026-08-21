# Steam 状态监控

适用于 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 的 Steam 状态监控插件。插件按群维护玩家列表，定时查询 Steam 状态，并以图片消息播报开始游戏、结束游戏、在线状态、成就与游戏时长排行。

- 当前版本：`3.3.3`
- 最低 AstrBot 版本：`4.24.2`

## 功能

- **分群状态监控**：每个群独立维护 SteamID 列表、启停状态和推送会话。
- **状态变更播报**：检测玩家开始游戏、切换游戏和结束游戏，并生成包含头像、封面、游玩时长等信息的图片。
- **智能轮询**：根据玩家最近活跃程度动态调整轮询间隔，也可配置固定间隔。
- **批量查询**：合并 Steam 玩家状态请求，降低 API 调用频率。
- **Steam 输入解析**：支持 17 位 SteamID64、好友码、个人资料链接和自定义 ID 链接。
- **用户绑定与查询**：添加玩家时可绑定聊天平台用户，通过 `/steamwho` 或 `/在干嘛` 查询状态。
- **成就推送**：轮询游戏成就，在玩家解锁新成就时生成图片通知；支持游戏过滤和成就黑名单。
- **游戏时长排行**：查看本群或全部群的今日、最近 7 天、最近 30 天或自定义天数排行。
- **每日排行推送**：在指定时间向已启用的群推送昨日排行，可选择分群统计或全局统计。
- **联动推送**：同一个 SteamID 可向其他群同步状态通知，而无需重复轮询。
- **管理页面**：通过 AstrBot Plugin Pages 注册 Steam 监控管理接口与页面资源。
- **本地持久化与缓存**：保存群组、绑定、排行、通知会话和运行状态，并缓存头像、封面及成就图标。

## 运行要求

- AstrBot `>= 4.24.2`
- Python 版本以当前 AstrBot 运行环境为准
- 可访问 Steam Web API、Steam Store API 和 Steam 社区相关接口
- 一个有效的 [Steam Web API Key](https://steamcommunity.com/dev/apikey)
- 可选：[SteamGridDB API Key](https://www.steamgriddb.com/profile/preferences/api)，用于补充游戏封面

Python 直接依赖记录在 `requirements.txt`：

- `httpx[socks]`：Steam API 请求及 SOCKS 代理支持
- `aiohttp`：异步下载成就图标
- `Pillow`：图片生成和处理
- `requests`：同步图片下载
- `numpy`：图片自动边界检测

插件不使用数据库，也不需要额外的数据库驱动或认证框架。AstrBot 本身由宿主环境提供，不在插件的 `requirements.txt` 中重复声明。

## 安装

### 通过 AstrBot 插件管理器

在 AstrBot 管理面板中安装本插件。如果插件市场中的版本不是本仓库版本，可使用仓库地址：

```text
https://github.com/OLRainM/astrbot_plugin_steam_status_monitor
```

安装后重启 AstrBot，或在插件管理页面重新加载插件。

### 手动安装

在 AstrBot 的插件目录中克隆仓库：

```bash
git clone https://github.com/OLRainM/astrbot_plugin_steam_status_monitor.git
```

如宿主未自动安装插件依赖，请在插件目录执行：

```bash
python -m pip install -r requirements.txt
```

然后重启 AstrBot 或重新加载插件。

> 不建议脱离 AstrBot 单独运行 `main.py`。根目录的 `main.py` 是 AstrBot 兼容入口，实际插件实现位于 `src/plugin/steam_status_monitor.py`。

## 配置

推荐在 AstrBot 插件配置页面修改配置。`steam_api_key` 是正常工作的必要配置；其他选项均有默认值。

### API 与网络

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `steam_api_key` | 空 | Steam Web API Key，必填。 |
| `sgdb_api_key` | 空 | SteamGridDB API Key；留空时优先使用 Steam 官方封面。 |
| `proxy` | 空 | HTTP 或 SOCKS 代理，例如 `http://127.0.0.1:7890`。 |
| `retry_times` | `3` | Steam API 请求失败后的重试次数。 |

### 监控与轮询

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `steam_ids` | 空列表 | 初始监控的 SteamID64 列表；也可通过指令按群添加。 |
| `notify_group_id` | 空 | 兼容配置项；通常由群内指令自动记录推送会话。 |
| `fixed_poll_interval` | `0` | 固定轮询间隔，单位秒；`0` 表示使用智能轮询。 |
| `smart_poll_intervals` | `1,3,5,10,20,30` | 智能轮询间隔，单位分钟，依次对应游戏中、12 分钟内、12 分钟至 3 小时、3 至 24 小时、24 至 48 小时、超过 48 小时。 |
| `online_poll_interval` | `5` | 在线但未游戏时的轮询间隔，单位分钟。 |
| `max_group_size` | `50` | 单群最多监控的 SteamID 数量。 |
| `quit_delay` | `120` | 检测到退出游戏后的确认延迟，单位秒，用于减少网络波动导致的误报。 |

### 排行、成就与游戏过滤

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `rank_push_hour` | `8` | 每日排行榜推送小时，范围 `0`–`23`。 |
| `rank_push_minute` | `0` | 每日排行榜推送分钟，范围 `0`–`59`。 |
| `achievement_poll_interval` | `120` | 游戏中成就轮询间隔，单位秒。 |
| `achievement_blacklist` | 空 | 不推送的成就 API 名称，多个值用英文逗号分隔。 |
| `game_filter_mode` | `全部游戏` | 可选 `全部游戏`、`黑名单` 或 `白名单`。过滤同时作用于状态播报和成就推送。 |
| `game_filter_ids` | 空 | Steam AppID 列表，多个值用英文逗号分隔。 |

配置示例：

```json
{
  "steam_api_key": "你的 Steam Web API Key",
  "sgdb_api_key": "",
  "proxy": "",
  "fixed_poll_interval": 0,
  "smart_poll_intervals": "1,3,5,10,20,30",
  "max_group_size": 50,
  "rank_push_hour": 8,
  "rank_push_minute": 0,
  "achievement_poll_interval": 120,
  "game_filter_mode": "全部游戏",
  "game_filter_ids": ""
}
```

不要将真实 API Key 提交到公开仓库。

## 使用方法

指令前缀由 AstrBot 决定，以下示例使用默认的 `/`。涉及修改、启停或清理的指令需要管理员权限。

### 快速开始

1. 在插件配置中填写 `steam_api_key`。
2. 在目标群添加玩家：

   ```text
   /steam addid 76561198000000000
   ```

3. 添加成功后，本群监控会自动启动；也可手动执行：

   ```text
   /steam on
   ```

4. 查看当前状态：

   ```text
   /steam list
   ```

### 玩家与绑定

```text
/steam addid 76561198000000000
/steam addid 12345678
/steam addid https://steamcommunity.com/id/example
/steam addid 76561198000000000 @用户 备注名
/steam delid 76561198000000000
/steamwho @用户
/在干嘛 @用户
```

多个 ID 可用中文或英文逗号分隔。`/steam delid` 还可追加群号，由管理员跨群删除玩家。

### 状态、排行与成就

```text
/steam list
/steam alllist
/steam alllist text
/steam rank
/steam rank week
/steam rank month
/steam rank 14
/steam allrank 7
/steam achievement_on
/steam achievement_off
```

排行榜以凌晨 `04:00` 作为每日统计边界。

### 每日排行推送

```text
/steam rank_on
/steam rank_on all
/steam rank_on list
/steam rank_on test
/steam rank_on del
/steam rank_on del 群号
```

- `/steam rank_on`：为当前群开启分群排行推送。
- `/steam rank_on all`：为当前群开启全局排行推送。
- `/steam rank_on test`：立即尝试推送昨日排行榜。

### 联动推送

在需要接收同步通知的群中执行：

```text
/steam push_group 76561198000000000
/steam delpush_group 76561198000000000
```

### 配置与维护

```text
/steam config
/steam set fixed_poll_interval 600
/steam off
/steam rs
/steam清除缓存
/steam clear_groupids 群号
/steam clear_allids
/steam help
```

- `/steam config` 会隐藏 API Key。
- `/steam rs`、`/steam clear_groupids` 和 `/steam clear_allids` 会清理状态或监控数据，请谨慎使用。
- `/steam openbox SteamID` 用于查看 Steam API 原始信息，可能产生较长输出。
- `test_*_render` 系列指令仅用于管理员排查图片渲染问题。

## 数据存储与隐私

插件通过 AstrBot 提供的数据目录保存运行数据，不依赖外部数据库。持久化内容包括：

- 各群监控的 SteamID 和启停状态
- 聊天平台用户与 SteamID 的绑定关系及备注名
- 通知会话、联动推送群和每日排行推送配置
- 玩家状态、游戏会话、游玩记录与成就快照
- 头像、头像框、游戏封面和成就图标缓存

这些数据通常以 JSON 和图片文件形式保存在 AstrBot 的插件数据目录中。API Key 由 AstrBot 插件配置管理。部署和备份时应限制数据目录访问权限，并避免公开配置文件。

插件会访问以下外部服务：

- Steam Web API
- Steam Store API
- Steam 社区与静态资源域名
- SteamGridDB（仅配置 API Key 或需要补充封面时）

Steam 资料或游戏详情不可见时，请检查目标玩家的 Steam 隐私设置。

## 项目结构

```text
.
├── main.py                     # AstrBot 兼容入口
├── src/
│   ├── application/services/   # 成就、列表与查询服务
│   ├── domain/ranking/         # 排行推送范围等领域逻辑
│   ├── infrastructure/         # Steam 客户端与本地持久化
│   ├── plugin/                 # 插件主体和指令
│   ├── presentation/           # 图片渲染与管理页面接口
│   └── shared/                 # 路径和通用工具
├── assets/                     # 字体、图片和文案资源
├── pages/steam-monitor/        # AstrBot Plugin Pages 前端资源
├── tests/                      # 单元与集成测试
├── _conf_schema.json           # AstrBot 配置定义
├── metadata.yaml               # 插件元数据
└── requirements.txt            # Python 运行时依赖
```

## 功能与文件地图

以下位置以当前源码为准，用于定位功能实现、数据边界和维护入口。

### 插件入口与核心编排

| 文件 | 类或关键方法 | 职责 |
| --- | --- | --- |
| `main.py` | `SteamStatusMonitorV3` | AstrBot 兼容入口，仅导出插件类。 |
| `src/plugin/steam_status_monitor.py` | `SteamStatusMonitorV3(PersistenceMixin, SteamClientMixin, Star)` | 插件生命周期、聊天指令、全局轮询、状态比较、通知、排行与成就编排。 |
| `src/plugin/steam_status_monitor.py` | `global_poll_and_log_loop()` | 合并各群玩家并批量轮询 Steam 状态，同时调度每日排行。 |
| `src/plugin/steam_status_monitor.py` | `check_status_change()` | 识别开始游戏、切换游戏和结束游戏，并触发记录及通知。 |
| `src/plugin/steam_status_monitor.py` | `_daily_rank_push()` | 按配置向目标群推送昨日分群或全局排行。 |
| `src/plugin/steam_status_monitor.py` | `terminate()` | 停止后台任务并强制保存运行数据。 |

### 应用服务与领域逻辑

| 文件 | 类或函数 | 职责 |
| --- | --- | --- |
| `src/application/services/achievement_monitor.py` | `AchievementMonitor` | 查询成就、比较快照、应用游戏过滤与成就黑名单，并渲染成就通知。 |
| `src/application/services/openbox.py` | `SteamOpenboxService` | 为 `/steam openbox` 查询并整理玩家 API 字段。 |
| `src/application/services/steam_list.py` | `SteamListService` | 汇总本群或全部群玩家状态，供列表指令渲染。 |
| `src/domain/ranking/push_scopes.py` | `build_rank_push_scopes()` | 规范化每日排行目标群，区分分群数据与全局数据并对群号去重。 |

### Steam 接口与本地持久化

| 文件 | 类或关键方法 | 职责 |
| --- | --- | --- |
| `src/infrastructure/clients/steam.py` | `SteamClientMixin` | 调用 Steam Web API 和 Store API，批量查询玩家状态，解析 SteamID64、好友码、资料链接及自定义 ID，并获取游戏名称和封面。 |
| `src/infrastructure/persistence/plugin_data.py` | `PersistenceMixin` | 在 `data/steam_status_monitor/` 下加载和保存 JSON 数据，同时维护字体缓存。 |

主要持久化文件如下：

| 文件模式 | 内容 |
| --- | --- |
| `steam_groups.json` | 各群监控的 SteamID 列表。 |
| `bind_data.json` | 聊天平台用户、SteamID 和备注名的绑定关系。 |
| `notify_sessions.json` | 各群的 AstrBot 通知会话。 |
| `push_groups.json` | SteamID 对应的联动推送群。 |
| `rank_push_groups.json` | 每日排行目标群及分群或全局统计模式。 |
| `play_records.json` | 按日期、玩家和游戏汇总的游玩分钟数，保存时清理超过 30 天的数据。 |
| `session_records.json` | 单次游戏会话，供甘特图和热力图使用，保存时清理超过 90 天的数据。 |
| `group_<群号>_states.json` | 分群玩家最后状态。 |
| `group_<群号>_start_play_times.json` | 分群玩家各游戏的开始时间。 |
| `group_<群号>_last_quit_times.json` | 分群玩家最后退出时间。 |
| `group_<群号>_pending_logs.json` | 分群待处理日志。 |
| `group_<群号>_pending_quit.json` | 分群延迟确认的退出状态。 |
| `group_<群号>_recent_games.json` | 分群最近游戏记录。 |

`/data/steam_status_monitor/` 已加入 `.gitignore`。部署、迁移或备份时仍应单独保护该目录，因为其中可能包含群号、用户绑定、SteamID、通知会话和活动记录。

### 图片渲染

| 文件 | 职责 |
| --- | --- |
| `src/presentation/renderers/game_start.py` | 开始游戏通知图片，以及头像、头像框和竖版封面处理。 |
| `src/presentation/renderers/game_end.py` | 结束游戏通知图片。 |
| `src/presentation/renderers/rank.py` | 游戏时长排行榜图片。 |
| `src/presentation/renderers/steam_list.py` | 玩家状态列表图片。 |
| `assets/` | 字体、默认图片和文案资源。 |

### 管理页面

| 文件 | 类或函数 | 职责 |
| --- | --- | --- |
| `src/presentation/web/admin_api.py` | `WebAdminAPI` | 向 AstrBot Plugin Pages 注册 Dashboard、排行榜图片、甘特图、热力图、群组、绑定、推送、配置、指令权限、玩家、封面和连通性测试接口。 |
| `src/presentation/web/response_cache.py` | `AsyncTTLCache` | 为统计响应提供 TTL 缓存、同键并发合并、取消保护和按名称失效。 |
| `src/presentation/web/statistics.py` | `build_dashboard_stats()`、`build_groups()`、`build_player_search_index()`、`build_heatmap_data()` | 在工作线程中构建 Dashboard、群组、玩家搜索和热力图数据，减少事件循环阻塞。 |
| `pages/steam-monitor/index.html` | — | Plugin Pages 页面结构。 |
| `pages/steam-monitor/app.js` | — | 页面交互、数据请求和视图更新。 |
| `pages/steam-monitor/style.css` | — | 页面样式。 |

`WebAdminAPI` 注册的接口覆盖以下能力：

- Dashboard 统计与排行榜图片；
- 游玩甘特图、全局热力图和单玩家热力图；
- 群组、群玩家和批量导入管理；
- 用户绑定的新增、更新和删除；
- 每日排行时间、目标群和统计范围设置；
- 插件配置读取与更新，读取时隐藏 API Key；
- 插件指令权限查询与更新；
- 玩家搜索、头像、详情和游戏封面；
- Steam API、Steam Store、SteamGridDB、封面和指定 SteamID 的连通性测试。

所有管理接口均通过 AstrBot 的 `context.register_web_api()` 注册到 `steam_status_monitor_V3` 插件命名空间，不额外启动独立 Web 服务。

### 测试覆盖

| 文件 | 覆盖范围 |
| --- | --- |
| `tests/unit/test_modular_structure.py` | 根入口轻量化、分层文件存在性和插件 Mixin 继承关系。 |
| `tests/unit/test_web_performance.py` | Web 统计缓存复用、失效、同键并发合并和统计结果兼容性。 |
| `tests/test_rank_push.py` | 每日排行按群或全局取数、目标群去重。 |

`tests/integration/` 当前仅保留目录结构，尚无集成测试文件。

## 开发与测试

克隆仓库并安装依赖：

```bash
git clone git@github.com:OLRainM/astrbot_plugin_steam_status_monitor.git
cd astrbot_plugin_steam_status_monitor
python -m pip install -r requirements.txt
```

运行测试：

```bash
python -m unittest discover -s tests -v
```

编译检查：

```bash
python -m compileall main.py src tests
```

完整运行测试需要 AstrBot 环境；不依赖 AstrBot 的单元测试可直接执行。

## 贡献指南

1. Fork 本仓库并从最新默认分支创建功能分支。
2. 修改前确认功能应位于 `application`、`domain`、`infrastructure`、`plugin`、`presentation` 或 `shared` 中的哪一层。
3. 保持根目录 `main.py` 作为轻量兼容入口，不要把业务逻辑重新堆回入口文件。
4. 新增配置时同步更新 `_conf_schema.json`、默认值读取逻辑和本文档。
5. 新增第三方库时同步更新 `requirements.txt`，不要添加未直接使用的依赖。
6. 为行为变更补充测试，并运行单元测试和编译检查。
7. 提交 Pull Request 时说明改动内容、原因、兼容性影响和验证结果。

请勿在 Issue、日志、截图或提交中泄露 Steam API Key、SteamGridDB API Key、群号、用户绑定或其他私有数据。

## 许可证

本项目使用仓库中 `LICENSE` 文件所声明的许可证。
