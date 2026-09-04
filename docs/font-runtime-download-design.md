# Steam 状态监控插件 - 字体运行时下载设计

## 1. 需求概述

AstrBot 插件市场要求发布 zip **不得超过 16MB**。当前仓库把 5 份全量 CJK 字体跟踪在 `assets/fonts/`，合计约 39MB，商店包会被 CI 直接拒绝。

本方案把字体从商店包中拆出：Git 与市场包只保留清单和空目录；完整字体以 GitHub Release 的单个 `fonts.zip` 分发；插件启动后在后台下载、校验、解压成五个独立文件，再交给现有 PIL 渲染路径。

约束来源：

- 官方文档「发布插件到插件市场 / 大小限制」：https://docs.astrbot.app/dev/star/plugin-publish.html
- 现有加载按文件名找 `NotoSansHans-*.otf` 与 `MiSans-*.ttf`，PIL `ImageFont.truetype` 需要磁盘路径，不能对着 zip 直接加载

---

## 2. 功能目标

### 2.1 必须做到

1. **商店包不含字体**：去掉 5 个字体文件后，已跟踪内容约 1MB，稳定低于 16MB。
2. **插件可先启动**：`__init__` 不下载 40MB；监控轮询、命令、WebUI 不因缺字体阻塞。
3. **后台补齐字体**：启动完成后按代理 / TLS 配置拉取 `fonts.zip`，校验 SHA256，解压为五个文件。
4. **渲染不中断**：字体未就绪时回退系统字体或 PIL 默认字体；就绪后下一次渲染自动切到全量 CJK。
5. **一次下载、一份校验**：主路径只有一个 zip + 一份 zip SHA256；五个独立文件只作 zip 失败后的补下备选。
6. **可观测、可重试、可取消**：状态可查，失败可退避重试，插件卸载时取消下载任务。

### 2.2 明确不做

- 不把字体 base64 进 Python 源码。
- 不把子集化字体打进商店包（玩家名 / 游戏名不可枚举，会出方块字）。
- 不把 `.gitattributes export-ignore` 当作唯一过审手段（clone 后再 zip 时无效）。
- 不在 `__init__` 里同步下载。
- 不把 zip 长期留在数据目录当字体源。
- 不在本方案里做字体热切换配置面板的完整产品化（WebUI 只做只读状态，命令做运维入口）。

---

## 3. 现状与缺口

### 3.1 当前字体文件

| 文件 | 约体积 | 用途 |
|---|---|---|
| `NotoSansHans-Regular.otf` | 7.98 MB | 开始/结束卡、排行榜、成就卡正文 |
| `NotoSansHans-Medium.otf` | 8.00 MB | 加粗标题 |
| `MiSans-Regular.ttf` | 7.70 MB | Steam 列表风格优先字体 |
| `MiSans-Bold.ttf` | 7.59 MB | 列表加粗 |
| `MiSans-Light.ttf` | 7.75 MB | 列表细体 |

### 3.2 当前加载路径互相不一致

1. `PersistenceMixin._ensure_fonts()` 在 `__init__` **最前面**调用，此时 `data_dir`、`proxy`、TLS 都还没初始化。注释写「检测/下载」，实现只把 Noto 两份从 `assets/fonts` 复制到 `data/steam_status_monitor/fonts`。
2. `PersistenceMixin.get_font_path()` 只认 Noto Regular / Medium。
3. `game_start.get_font_path()` 只查 `assets/fonts` 和渲染器目录，不查数据目录。
4. 开始卡 / 结束卡 / 成就卡直接拼 `FONTS_DIR / NotoSansHans-*.otf`。
5. `steam_list.load_font()` 优先 MiSans，缺失再回退 Noto / 系统字体。
6. 单元测试 `test_modular_structure.py` 把 `assets/fonts/NotoSansHans-Regular.otf` 当成仓库必有文件。

结论：现有「字体自动管理」只覆盖开发克隆（仓库里已经有字体）的复制，没有下载、没有完整性校验、没有统一解析入口。商店包去掉字体后，多数渲染会落到 PIL 默认字体或系统字体，中文必乱码。

### 3.3 启动顺序问题

当前 `__init__` 顺序：

```text
_ensure_fonts()
→ 读 config / 配 TLS / 配 proxy
→ 设 data_dir
→ 拉起轮询任务
```

下载方案必须把字体准备挪到 **proxy、TLS、data_dir 都就绪之后**，并且改成后台任务。

---

## 4. 方案选型

| 方案 | 商店包体积 | 首次渲染质量 | 复杂度 | 结论 |
|---|---|---|---|---|
| A. 继续内嵌 5 个字体 | 约 40MB，过不了 16MB | 最好 | 最低 | 否决 |
| B. 子集化后内嵌 | 可能压进 16MB | 玩家名/游戏名缺字 | 中 | 否决 |
| C. 商店包不带字体，启动后下 `fonts.zip` | 约 1MB | 下载完成前降级，完成后全量 | 中 | **采用** |
| D. 五个独立 Release 文件作主路径 | 约 1MB | 同 C，但要下五次 | 高 | 仅作 zip 失败备选 |
| E. 联系维护者 bypass 16MB | 仍 40MB | 最好 | 最低 | 仅作上架过渡，不替代 C |

采用 **C 为主、D 为备选、E 为短期上架通道**。

---

## 5. 架构总览

```text
GitHub Release
  fonts.zip
  fonts.zip.sha256
        │
        ▼
FontPackService  (src/infrastructure/fonts/pack_service.py)
  下载 / 校验 / 解压 / 原子替换 / 重试
        │
        ▼
data/steam_status_monitor/fonts/
  NotoSansHans-Regular.otf
  NotoSansHans-Medium.otf
  MiSans-Regular.ttf
  MiSans-Bold.ttf
  MiSans-Light.ttf
  manifest.local.json
        │
        ▼
FontResolver  (src/shared/fonts.py)
  解析顺序：bundled → data_dir → 系统字体 → PIL default
        │
        ▼
全部渲染器 / PersistenceMixin.get_font_path / WebAdmin
```

插件主类只负责：创建服务、在初始化末尾 `ensure_ready()`、在 `terminate()` 里取消任务。不再在 `__init__` 开头复制字体。

---

## 6. 模块划分

### 6.1 新增

| 模块 | 路径 | 职责 |
|---|---|---|
| 字体清单 | `assets/fonts/manifest.json` | 版本、zip URL、zip SHA256、五个文件的 name/size/sha256、可选单文件 URL |
| 解析器 | `src/shared/fonts.py` | 统一 `resolve_font_path(name, *, bold=False)`；进程内路径缓存；下载完成后 `invalidate()` |
| 包服务 | `src/infrastructure/fonts/pack_service.py` | 状态机、后台下载、zip/文件校验、zip-slip 防护、原子落盘、重试 |
| 状态查询 | 同上，只读快照 | 给命令和 WebUI 用 |

### 6.2 改造

| 模块 | 改什么 |
|---|---|
| `src/shared/paths.py` | 保留 `FONTS_DIR`；新增 `FONT_MANIFEST_PATH`、`REQUIRED_FONT_FILES` |
| `src/infrastructure/persistence/plugin_data.py` | `_ensure_fonts()` 改为委托 `FontPackService`；`get_font_path()` 改为调 `FontResolver` |
| `src/plugin/steam_status_monitor.py` | 删除 `__init__` 开头的 `_ensure_fonts()`；在 `data_dir` / proxy / TLS 就绪后创建服务并 `asyncio.create_task`；`terminate()` 取消下载任务 |
| `src/presentation/renderers/*.py` | 开始/结束/列表/排行榜/成就卡全部走 `resolve_font_path`，禁止再硬编码 `FONTS_DIR / 文件名` |
| `_conf_schema.json` | 增加下载开关和可选自定义 URL |
| `tests/unit/test_modular_structure.py` | 不再要求仓库内存在 `.otf`；改为要求 `manifest.json` 与 `.gitkeep` |
| `README.md` | 说明商店包不含字体、首次启动会后台下载、可用命令查看状态 |

### 6.3 不改

- 渲染布局、字号、拉长画布策略
- Steam API / 会话状态机
- HTTP 连接池方案（字体下载用独立短生命周期客户端，避免和尚未落地的池化方案耦合）

---

## 7. 数据与文件布局

### 7.1 仓库内（进商店包）

```text
assets/fonts/
  .gitkeep
  manifest.json
```

`manifest.json` 示例：

```json
{
  "schema_version": 1,
  "pack_version": "2026.09.04",
  "zip": {
    "url": "https://github.com/Maoer233/astrbot_plugin_steam_status_monitor/releases/download/fonts-bundle/astrbot_plugin_steam_status_monitor-fonts.zip",
    "sha256": "<64 hex>",
    "max_bytes": 83886080
  },
  "files": [
    {"name": "NotoSansHans-Regular.otf", "sha256": "<64 hex>", "size": 8368600},
    {"name": "NotoSansHans-Medium.otf", "sha256": "<64 hex>", "size": 8388608},
    {"name": "MiSans-Regular.ttf", "sha256": "<64 hex>", "size": 8074032},
    {"name": "MiSans-Bold.ttf", "sha256": "<64 hex>", "size": 7958528},
    {"name": "MiSans-Light.ttf", "sha256": "<64 hex>", "size": 8126464}
  ],
  "fallback_files": [
    {"name": "NotoSansHans-Regular.otf", "url": "https://.../NotoSansHans-Regular.otf"}
  ]
}
```

`fallback_files` 可空。主路径只用 zip。

### 7.2 运行时数据目录

```text
data/steam_status_monitor/fonts/
  NotoSansHans-Regular.otf
  NotoSansHans-Medium.otf
  MiSans-Regular.ttf
  MiSans-Bold.ttf
  MiSans-Light.ttf
  manifest.local.json          # 已安装 pack_version + 各文件 sha256
  .download/
    fonts.zip.part             # 流式下载临时文件
    extract/                   # 解压临时目录
```

成功后删除 `.download/` 和 zip。失败保留 `.part` 仅用于断点续传（第一期可不做续传，直接重下）。

### 7.3 本地状态快照

`manifest.local.json`：

```json
{
  "pack_version": "2026.09.04",
  "installed_at": 1757000000,
  "files": {
    "NotoSansHans-Regular.otf": {"sha256": "...", "size": 8368600}
  }
}
```

用途：启动时快速判断「已经装过且版本匹配」，避免每次启动全量哈希。若文件缺失或 size 对不上，再做 SHA256。

---

## 8. 状态机

```text
MISSING ──ensure_ready()──► CHECKING
                              │
                 本地完整且版本匹配
                              ├──► READY
                              │
                         需要下载
                              ├──► DOWNLOADING ──成功──► READY
                              │         │
                              │      失败/取消
                              │         ├── 可重试 ──► BACKOFF ──到期──► DOWNLOADING
                              │         └── 永久失败（清单无效/校验连续失败）──► FAILED
                              │
用户关闭下载 ──► DISABLED
```

不变量：

- 同一时刻最多一个下载任务。
- `READY` 表示五个必需文件都在 `data_dir/fonts` 或 `assets/fonts`，且哈希（或开发态 bundled）通过。
- 渲染路径**不等待**状态机；只读当前能解析到的路径。
- `terminate()` 把任务取消后，状态回到 `MISSING` 或保留已落盘的 `READY`，不留半截 zip 当字体。

---

## 9. 数据流转

### 9.1 启动

```text
插件 __init__
  读配置、TLS、proxy、data_dir
  创建 FontResolver(data_dir, bundled_dir)
  创建 FontPackService(resolver, manifest, proxy, tls)
  若 bundled 已有全部文件 → 状态 READY，不下载
  否则 create_task(service.ensure_ready())
  继续拉起轮询 / WebUI
```

### 9.2 下载成功

```text
GET zip（流式，限 max_bytes）
  → 写 fonts.zip.part
  → sha256(zip) == manifest.zip.sha256
  → 解压到 .download/extract/（拒绝 .. 与绝对路径）
  → 逐文件 sha256 / size
  → 原子替换到 fonts/（先写 *.tmp 再 replace）
  → 写 manifest.local.json
  → 删 .download/
  → resolver.invalidate()
  → 状态 READY
```

### 9.3 渲染

```text
render_*()
  → resolve_font_path("NotoSansHans-Regular.otf")
      1. assets/fonts/<name> 存在则用（开发克隆 / 手动放置）
      2. data_dir/fonts/<name> 存在则用
      3. 系统候选（微软雅黑 / Noto CJK / 文泉驿）
      4. ImageFont.load_default()
  → 不抛「缺字体」异常；只打一次降级日志
```

### 9.4 失败备选（zip 主路径失败后）

仅当 zip 连续失败，且 `fallback_files` 非空：

```text
对缺失文件逐个 GET
  → 单文件 sha256
  → 原子写入
  → 五个都齐则 READY
```

任一单文件失败不影响已成功的文件；下次重试只补缺失项。

---

## 10. 交互逻辑

### 10.1 对普通群用户

无新命令。开始/结束/列表卡照常发。字体未就绪时可能中文显示为方块或系统字体，下载完成后自动恢复，无需用户操作。

### 10.2 对管理员

| 命令 | 权限 | 行为 |
|---|---|---|
| `/steam fonts` | 群管理员或机器人管理员 | 返回状态、pack 版本、已就绪文件数、失败原因、下次重试时间 |
| `/steam fonts download` | 同上 | 若 `DISABLED` 则拒绝；否则取消退避并立即重试。已 `READY` 时提示无需下载。**显示实时进度，每 5% 更新一次消息** |
| `/steam fonts clean` | 同上 | 清理 `data_dir/fonts/` 与 `.download/`，下次启动重新下载（可选，用于修复损坏缓存） |

**下载进度可视化**（`/steam fonts download` 专用）：

```text
正在下载字体包...
▓▓▓▓▓░░░░░░░░░░░░░░░ 25% (10.2 MB / 40.8 MB)
预计剩余时间: 18 秒
```

- 进度条长度 20 字符，每 5% 一跳（`▓` 表示已完成，`░` 表示未完成）
- 百分比精确到整数
- 已下载 / 总大小显示为 MB，保留 1 位小数
- 预计剩余时间基于最近 10 秒的平均速度计算
- 每次更新**编辑同一条消息**而非发送新消息（避免刷屏）
- 100% 完成后显示：`✓ 字体安装成功 (40.8 MB, 用时 45 秒)`

实现约束：
- 使用 `context.update_message()` 或等效 API 原地更新（若平台不支持则每 10% 发一条新消息）
- 下载流中每接收 2MB 或间隔 >2 秒时触发一次进度回调
- 进度回调不阻塞下载主流程；若更新消息失败仅记录日志

不在群里主动推送「正在下载字体」通知，避免每个群一条。需要时用命令查。

### 10.3 WebUI（第二期）

管理页只读展示：状态、版本、五个文件是否存在。不提供上传字体（避免未校验的任意文件进数据目录）。手动放置仍允许：把五个文件拷进 `data/steam_status_monitor/fonts/` 后，下次 `ensure_ready()` 校验通过即 `READY`。

### 10.4 配置项

| 配置 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `font_download_enabled` | bool | true | 关闭后不联网；仅用 bundled / 已缓存 / 系统字体 |
| `font_pack_url` | string | `""` | 非空则覆盖清单里的 zip URL；仍用清单 SHA256 校验。URL 必须 https |
| `font_download_timeout_sec` | int | 120 | 整包下载超时 |

修改后重启 / 重载生效，与现有网络配置一致。

---

## 11. 每个细节如何完善

### 11.1 边界情况

| 场景 | 处理 |
|---|---|
| 开发仓库仍保留字体 | bundled 优先，不下载。方便本地 Docker 挂载开发目录 |
| 商店包无字体、数据目录也无 | 后台下载；渲染先降级 |
| 数据目录已有完整且版本匹配的字体 | 启动时 size 快检 + 读 `manifest.local.json`，不重复下载 |
| 数据目录有旧版或缺文件 | 视为不完整，重新下 zip，原子替换，不混用新旧文件 |
| 磁盘空间不足 | 下载前检查可用空间 ≥ `zip.max_bytes + 各文件 size 之和 + 16MB` 余量；不足则 `FAILED`，日志明确 |
| 数据目录只读 / 权限不足 | 捕获 `OSError`，`FAILED`，渲染走系统字体 |
| 清单缺失或 JSON 损坏 | 不下载，`FAILED`，日志指出清单问题 |
| 自定义 `font_pack_url` 与清单 SHA256 不一致 | 校验失败，不安装，避免装入被替换的恶意包 |
| 代理未开、GitHub 不可达 | 按现有 `enable_proxy` / `proxy_url` 走；失败进入 BACKOFF |
| SOCKS 代理但未装 socksio | 沿用现有自动安装逻辑；字体任务在 proxy 初始化之后启动 |
| 插件重载 | `terminate()` 取消任务；新实例重新 `ensure_ready()`。已 `READY` 的文件保留 |
| 多实例误启动 | 现有 `_ssm_running` 仍拦截；字体任务挂在插件实例上，不设进程级全局单例 |
| zip 内多一层目录 `fonts/*.otf` | 解压后按 basename 匹配清单文件名；忽略目录前缀 |
| zip 内含多余文件 | 只取出清单列出的五个；其余丢弃 |
| 文件名大小写 | Windows 上按大小写不敏感匹配，落盘强制用清单中的规范文件名 |
| 部分渲染器只要 Noto、列表还要 MiSans | 五个都列为必需。Noto 齐但 MiSans 缺时，列表走 Noto/系统字体，状态仍为「不完整」，继续补 |

### 11.2 异常场景

| 异常 | 处理 |
|---|---|
| HTTP 404 / 410 | 不立即无限重试；记 `FAILED`，退避上限 6 小时，最多记 5 次后停止直到手动 `/steam fontdownload` 或重启 |
| HTTP 429 / 5xx / 超时 / 连接重置 | 指数退避：1m、5m、15m、30m、60m |
| 下载中途断开 | 删除 `.part`，下次整包重下（第一期不做 Range 续传，避免校验复杂） |
| zip SHA256 不符 | 删除临时文件，视为可重试失败；连续 3 次哈希失败则停到手动触发，防止一直拉坏包 |
| 单文件 SHA256 不符 | 整包视为失败，不安装任何未校验文件 |
| Zip bomb / 超 `max_bytes` | 流式计数，超限中止并删除临时文件 |
| Zip slip（`../`、绝对路径） | 解压前检查每个 member 的规范化路径必须落在 extract 目录内 |
| 解压后找不到清单文件 | 失败，不把 extract 目录提升为 fonts |
| `ImageFont.truetype` 仍失败 | 捕获后回退 `load_default()`，这条渲染成功发出，不让通知链路因字体崩溃 |
| 下载任务与 `terminate()` 竞态 | 任务响应 `CancelledError`；落盘用临时文件 + `os.replace`，取消时清理 `.download/` |
| 渲染线程读文件、下载线程写文件 | 只在校验完成后 `os.replace` 到最终文件名；Windows 上 replace 目标已存在也可覆盖。解析器缓存的是路径字符串，文件替换后 inode 变化不影响下次 `truetype` 打开 |

### 11.3 用户体验

1. **启动不卡死**：AstrBot 重载后监控立刻恢复，不等 40MB 下完。
2. **不刷屏**：不向业务群发「正在下载字体」。只写插件日志。
3. **降级可理解**：首次缺字体时打一条 warning：`CJK 字体未就绪，卡片可能出现方块字，正在后台下载`。同一进程只打一次。
4. **运维可查**：`/steam fontstatus` 用中文短句说明：未下载 / 下载中 / 已就绪 / 失败原因。
5. **手动逃生**：管理员可把五个文件拷进数据目录，或开 `font_download_enabled=false` 后自行放置。
6. **开发体验**：本地 git 克隆若仍有字体，零网络开销。CI 与商店包用空 `assets/fonts`。
7. **下载完成后无需重启**：`invalidate()` 后下一张卡即用新字体。

### 11.4 安全

- 只允许 `https://`。
- 默认 URL 钉在官方仓库 Release；自定义 URL 仍必须通过清单 SHA256。
- 不执行 zip 内任何脚本，只取字体字节。
- 日志不打印完整自定义 URL 查询串；走现有 `redact_sensitive()`。
- 不接受用户通过聊天上传的字体文件。

### 11.5 性能

- 下载与主轮询并行，独立 `httpx.AsyncClient`，timeout 与 Steam API 分离。
- 不占用成就轮询 / 头像下载的并发槽。
- 启动快检只读 size + `manifest.local.json`，避免每次启动对 39MB 做 SHA256。
- 解析器缓存路径，下载完成才清空。
- 峰值磁盘约 `zip + 解压 + 最终文件`，成功后降回约 39MB。

---

## 12. 渲染接入约定

所有渲染入口只允许通过：

```python
from src.shared.fonts import resolve_font_path, load_truetype

font = load_truetype("NotoSansHans-Regular.otf", 22)
bold = load_truetype("NotoSansHans-Medium.otf", 28)
list_font = load_truetype("MiSans-Regular.ttf", 18, fallbacks=["NotoSansHans-Regular.otf"])
```

`load_truetype(name, size, fallbacks=())` 内部：

1. `resolve_font_path(name)`
2. 失败则依次 `fallbacks`
3. 再试系统字体映射表
4. 最后 `ImageFont.load_default()`

系统字体映射（按平台）：

- Windows：`msyh.ttc` / `msyhbd.ttc`
- Linux：`NotoSansCJK-Regular.ttc`、`wqy-microhei.ttc`、`NotoSansSC-Regular.otf`
- macOS：`PingFang.ttc`、`STHeiti Light.ttc`

列表渲染继续「MiSans 优先、Noto 回退」，但候选路径全部由解析器提供，不再自己 `os.path.join(FONTS_DIR, name)`。

`PersistenceMixin.get_font_path()` 保留方法名，实现改为 `resolve_font_path`，避免主类和通知 mixin 大面积改调用点。

---

## 13. 发布与仓库策略

### 13.1 Git

- 从 Git 跟踪中移除五个字体文件（`git rm --cached`），保留历史中的 blob，不强制改写历史。
- `assets/fonts/.gitkeep` + `manifest.json` 进库。
- `.gitignore` 增加 `assets/fonts/*.otf`、`assets/fonts/*.ttf`、`assets/fonts/*.ttc`，避免开发者再次提交。
- 本地开发需要字体时：手动拷回，或跑一次插件让它下载到数据目录。

### 13.2 GitHub Release

建议独立 tag，例如 `fonts-2026.09.04`，与插件版本解耦。字体不变就不发新包。

Release 资源：

- `fonts.zip`（五个文件在 zip 根目录或统一 `fonts/` 子目录）
- 发布说明里写 SHA256

生成 zip 的维护命令（文档给出，不强制进 CI）：

```text
python -c "..."  # 计算 sha256、写回 manifest.json
```

### 13.3 商店包

Cloud 发布流水线打的 zip 不再含字体。若维护者仍用「克隆整仓再压缩」，`.gitignore` 不能救已经跟踪的文件，所以必须先 `git rm --cached`。

短期若 V4.4.4 要先上架，仍可走维护者 bypass；本方案落地后不再依赖 bypass。

---

## 14. 测试计划

| 用例 | 断言 |
|---|---|
| 清单解析 | 缺字段 / 非法 sha256 / 非 https URL 被拒绝 |
| bundled 齐全 | 不发起 HTTP |
| 数据目录齐全且版本匹配 | 不发起 HTTP |
| 缺字体时下载 zip | 校验通过后五个文件就位，状态 READY |
| zip 哈希错误 | 不安装，状态 FAILED/BACKOFF，数据目录无半截字体 |
| zip-slip member | 解压拒绝，无文件写出到 fonts 外 |
| 超过 max_bytes | 中止，删除 .part |
| 取消任务 | `.download/` 被清理 |
| 渲染缺字体 | `load_truetype` 返回 default，不抛 |
| 下载完成后 invalidate | 下一次 resolve 拿到新路径 |
| 单文件 fallback | zip 失败后只补缺失文件 |
| 结构测试 | 仓库不再要求 `.otf` 存在，要求 `manifest.json` 存在 |

测试用很小的假 zip / 假 otf 字节，不把 39MB 字体放进 CI。

---

## 15. 落地阶段

### 阶段 0：发布字体包（不改运行时）

1. 用当前五个文件打 `fonts.zip`，算 SHA256。
2. 发 GitHub Release `fonts-2026.09.04`。
3. 提交 `assets/fonts/manifest.json`。

### 阶段 1：MVP（建议一次 PR）

1. `FontResolver` + `FontPackService`。
2. 插件启动末尾后台 `ensure_ready()`，`terminate()` 取消。
3. 渲染统一走解析器。
4. `/steam fontstatus`、`/steam fontdownload`。
5. 配置三项。
6. 单测覆盖校验、zip-slip、取消、降级。
7. `git rm --cached` 五个字体；改结构测试与 README。

### 阶段 2：体验增强

1. WebUI 只读状态。
2. zip 失败后的单文件 fallback。
3. 可选断点续传（Range + 分段哈希），仅当真实环境 Groub 下载不稳定再做。

### 阶段 3：后续扩展（预留，本方案不实施）

见下一节。

回滚：恢复五个字体进仓库、删服务、解析器退回 `FONTS_DIR`。数据目录里已下载的字体可留着，无害。

---

## 16. 后续可扩展性

设计时把「包格式」和「解析」分开，后面这些需求不必推翻主路径：

1. **换字体族**：只改清单和 zip，解析器仍按文件名。若引入新文件名，在 `REQUIRED_FONT_FILES` 加一项，渲染器按需引用。
2. **多镜像**：`manifest.zip.mirrors: [url, ...]`，主 URL 失败后按序尝试，校验仍用同一 SHA256。
3. **按需下载**：若未来只想强制 Noto、MiSans 作可选，把清单拆成 `required` / `optional`；列表渲染在 optional 缺失时保持现有 Noto 回退。
4. **版本升级**：`pack_version` 比较；新包下载成功后原子替换整组，避免 Regular 新、Medium 旧。
5. **用户自定义字体目录**：可加 `font_extra_dir`，解析顺序变成 extra → bundled → data_dir → 系统。仍不从聊天收文件。
6. **与 HTTP 连接池合流**：等实例级 `HTTPClientProvider` 落地后，字体下载可改用 media 客户端 + 独立 Semaphore，不改状态机。
7. **商店包字体策略变化**：若官方放宽或提供资源托管，只需把 `ensure_ready()` 的远程源换成新 URL，清单机制保留。

扩展时保持三条不变：

- 渲染永远不阻塞下载
- 只安装哈希通过的文件
- zip 是主分发物，散文件只是备选

---

## 17. 风险与取舍

| 风险 | 影响 | 缓解 |
|---|---|---|
| GitHub Release 在国内不稳定 | 首次启动较长时间中文乱码 | 走用户已配的代理；镜像字段预留；允许手动拷贝 |
| 39MB 对小磁盘 / 嵌入式主机偏大 | 下载失败 | 预检磁盘；失败不反复写满磁盘 |
| 去掉仓库字体后开发者第一次 clone 没有字体 | 本地卡片乱码直到下载完 | README 写明；bundled 若存在则跳过下载 |
| 结构测试、文档、截图仍写死 otf 路径 | CI 红 | 阶段 1 一并改 |
| 维护者仍把整仓字体打进商店 zip | 继续超 16MB | 必须 `git rm --cached`，不能只靠 ignore |
| 自定义 URL 被改成恶意包 | 任意代码不可执行，但仍可能装入坏字体导致渲染异常 | 强制清单 SHA256；https only |

---

## 18. 推荐默认（避免实施时悬空）

若实施时没有新的产品意见，按下列默认落地：

1. 主路径：单个 `fonts.zip` + SHA256。
2. 五个文件全部视为必需。
3. 第一期不做断点续传、不做 WebUI 上传、不做群内下载进度通知。
4. 第一期可以不做单文件 fallback，但清单预留 `fallback_files`。
5. `__init__` 开头的 `_ensure_fonts()` 删除，改为初始化末尾后台任务。
6. 字体包 tag 与插件版本解耦。

---

## 19. 验收标准

1. 不含字体的插件 zip < 16MB。
2. 干净数据目录下，插件能在字体下载完成前响应 `/steam` 命令并继续轮询。
3. 下载并校验成功后，开始卡 / 结束卡 / 列表 / 排行榜能渲染中文玩家名与游戏名。
4. 损坏 zip 不会留下可被 `truetype` 打开的半截字体。
5. `terminate()` 后无残留下载任务。
6. 仓库 `git ls-files assets/fonts` 不含 `.otf/.ttf`，含 `manifest.json` 与 `.gitkeep`。
