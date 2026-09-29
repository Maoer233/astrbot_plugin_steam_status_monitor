# ADR：价格查询留在现有边界，不新建聚合器和解析器

- 状态：已落地
- 日期：2026-09-27
- 版本：4.8.4
- 范围：`/steam price`、`/steam px`、`/steam game` 的价格查询链路
- 对照：`docs/adr/adr-command-layer-split.md`（命令层只做薄适配）；`docs/adr/adr-inline-passthrough-helpers.md`（禁止空壳转发）；`docs/design/price-and-ranking-ownership.md`（价格编排归属）

本文只记录 **价格查询该停在哪一层、哪些类不再新建**。阶段清单和未完成项留在本地跟踪稿，不入库。

## 1. 背景

4.8.0 已把价格用例抽到 `PriceQueryService`，命令层只解析事件并渲染结果。后续修复暴露出另一类风险：币种、区域、搜索身份和失败状态散落在摘要字典里，调用方可能把金额按错误币种解释，或把上游失败伪装成“未找到游戏”。

修复时出现过继续拆类的方案，包括：

- `PriceAggregator`、`PriceCardAssembler`、完整 ViewModel
- `AsyncHttpGateway`、`SteamSearchClient`
- `GameResolver`、`GameIdentity`、`SearchResult`、`SearchSelection`

这些名字能描述职责，但当前每条规则只有一个调用方。再包一层只会让测试多 mock 一次，不能减少币种或区域错误。

## 2. 决策

**价格规则留在已有对象的私有方法里。新增行为必须挂到现有边界，不为目录对称或单次调用新建类。**

| 现有边界 | 负责 | 不负责 |
| --- | --- | --- |
| `PriceQueryService` | 设置校验、搜索编排、价格来源选择、核心/增强预算、组 `PriceCard` | 直接发 HTTP；解析 `AstrMessageEvent` |
| `ITADClient` | ITAD 请求、连接复用、搜索关联、去重、AppID 绑定、价格摘要 | 决定卡片展示哪一个价格 |
| `SteamStoreClient` | 商店详情、评价、区域价、锁区/年龄墙和结构化商店错误 | 把回退价改写成主区价 |
| `summary_to_currency()` | 按各来源币种把金额折到主货币，并同步币种字段 | 选择 Steam 价还是第三方价 |
| `presentation/commands/store.py` | 剥命令、候选序号、把失败变成用户文案、出图 | 区价循环、币种换算、搜索去重 |
| `render_game_detail_image()` | 格式化已经选定的金额、区域和来源 | 调用 `convert()` 或重判价格来源 |

明确不再新建：

- `PriceAggregator`：当前价和史低各只有一条固定优先级，留在 `_select_current_price()` 与 `_select_history_low()`。
- `PriceCardViewModel` / `PriceCardAssembler`：`PriceCard` 继续携带已选价格、来源、请求区和实际区。
- `SteamSearchClient`：Steam 搜索请求留在 `ITADClient` 私有方法。
- `GameResolver`：关联、去重和 AppID 绑定留在 `search_games()`。
- `GameIdentity`：用 `ITADGame.itad_id` 排除 `steam:<appid>`，不另建身份对象。
- `SearchResult`：搜索状态挂在现有返回 dict，区分成功、空结果和上游失败。
- `SearchSelection`：候选过期和查询编号留在现有候选缓存。
- `AsyncHttpGateway`：连接复用留在 ITAD 与 Steam Store 客户端，不抽通用网关。

已提交的私有取数方法不回退。它们已经是缓存、预算和错误分类的挂载点，再内联会把编排和 HTTP 重新缠在一起。

## 3. 备选方案

### 3.1 为每个价格来源建策略对象

Steam、ITAD 史低、第三方商店和区域回退各自实现一个 Policy，再由聚合器排序。

否决原因：来源优先级是固定规则，不是可插拔策略。策略对象不能解决“金额和币种必须成对”的问题，反而让一次查价穿过更多无状态包装。

### 3.2 把搜索、身份和选择结果都做成独立类型

用 `SearchResult`、`GameIdentity`、`SearchSelection` 替换现有 dict 和 `ITADGame`。

否决原因：需要约束的是 `steam:<appid>` 不得进入 ITAD 价格接口，以及失败状态不得伪装成空结果。这两个约束已经能挂在现有返回值上。新建类型不会减少外部请求，只会扩大本次发布的迁移面。

### 3.3 抽统一 HTTP 网关后再加缓存

先把 ITAD、Steam Store 和其余 Steam API 收进 `AsyncHttpGateway`，再统一做 TTL 与 in-flight 合并。

否决原因：价格查询的连接复用和同键合并只需要 ITAD 与 Steam Store 两个客户端。其余 Steam API 的缓存不是本次发布门槛，不应被网关重构绑在一起。

## 4. 落地规则

1. 金额转换成功后，必须同步更新对应币种字段。未知币种保留原金额和原币种，不能静默标成主货币。
2. `cdk_amount` 只跟 `cdk_currency` 成对变化，不能借用顶层 `currency`。
3. `steam:<appid>` 只用于 Steam 商店直查。lookup 失败可以出 Steam-only 卡片，但不能把临时身份发给 ITAD 价格接口。
4. 主区无价才按港、台、日、美回退。卡片必须保留请求区和实际区；回退价不能显示成主区可购买价。
5. 搜索超时、未配置、限流、鉴权失败和上游异常必须与“未找到游戏”分开。命令层只展示稳定中文文案，不暴露上游响应细节。
6. 核心价格使用 12 秒预算。对比区和评价只使用总预算剩余时间；增强数据失败或超时不取消核心卡片。
7. 同一价格键的并发请求复用 in-flight task。失败、取消和结束后清理，避免下一次查询复用已失败任务。
8. 候选缓存 3 分钟过期。有效查询不被新查询覆盖；过期序号必须提示重新查询。

## 5. 结果

4.8.4 发布时，价格相关测试 94 项通过，另有 13 个子测试通过。Renderer 不再对价格金额做业务换算。`/steam price`、`/steam px`、`/steam game` 的入口和权限保持不变。

本次不包含：

- 搜索、翻译、ITAD lookup 和卡片构建的共享总预算。
- 按每个错误码继续细分卡片降级文案。
- 搜索结果、游戏身份、评价和汇率缓存。
- 其余 Steam API 的通用异步连接层。

这些可以继续挂在本 ADR 确定的边界上，不需要先新增类。
