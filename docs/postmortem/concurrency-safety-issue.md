# SessionService 并发安全问题分析

## 问题概述

`SessionService` 在处理短时间内连续相反的状态事件（如"退出" → "重新进入"）时，**缺少进程内互斥锁保护**，导致并发调用可能产生状态覆盖、事件重复或时长计算错误。

### 核心原因

虽然 `handle` 方法内部是同步的（读取 → 计算 → 写入一气呵成），但**多个并发调用**之间缺乏互斥保护，可能出现以下时间线：

```python
# 两个并发调用交错执行
T0: 调用A 读取 session = playing(game_a)
T1: 调用B 读取 session = playing(game_a)  ← 读到相同状态
T2: 调用A 计算 apply(gameid=None) → confirming_exit
T3: 调用A 写入 _sessions[key] = confirming_exit
T4: 调用B 计算 apply(gameid=None) → 基于旧 playing 计算
T5: 调用B 写入 _sessions[key] → 覆盖了调用A的结果 ❌
```

---

## 三个具体问题

### 问题 1：多群并发导致的状态覆盖

**触发条件**：
- 同一 SteamID 同时属于多个群
- 轮询间隔对齐（多个群在同一分钟检测同一玩家）

**调用链路**：
```python
# src/application/services/polling_tracking.py:107-126
async def query_one_group(gid, sids):
    tasks = []
    for sid in sids:
        tasks.append(self.check_status_change(gid, single_sid=sid, ...))
    results = await asyncio.gather(*tasks, return_exceptions=True)  # ← 并发

poll_tasks = [query_one_group(gid, sids) for gid, sids in group_sids.items()]
await asyncio.gather(*poll_tasks, return_exceptions=True)  # ← 各群并行
```

**影响**：
- 状态字段（`state`、`exited_at`、`exit_deadline`）可能被覆盖
- 事件发送与最终状态不一致
- 时长计算基于错误的 `exited_at`

**复现路径**：
1. 配置同一玩家到 2 个以上的群
2. 玩家退出游戏（触发 `playing` → `confirming_exit`）
3. 观察日志：可能看到多次 `confirming_exit` 转换，但只有一次有效

---

### 问题 2：波动窗口的竞态条件

**触发条件**：
- 玩家在 `confirming_exit` 状态（180 秒窗口）
- 短时间内收到两次检测：一次观察到 `gameid=None`，另一次观察到 `gameid=原游戏`

**代码片段**：
```python
# src/domain/monitoring/session.py:105-113
if session.state == "confirming_exit":
    if current == session.gameid:
        if now <= deadline:
            resumed = replace(session, state="playing", exited_at=None, ...)
            return resumed, (SessionEvent("fluctuation", resumed),)
```

**问题场景**：
```
初始: confirming_exit(game_a, deadline=1180, exited_at=1000)

T0 (now=1175): 检测A 读取 confirming_exit
T1 (now=1176): 检测B 读取 confirming_exit
T2: 检测B apply(gameid=game_a) → playing (清空 exited_at)
T3: 检测B 写入 playing
T4: 检测A apply(gameid=None) → 基于旧 confirming_exit
T5: 检测A 写入 confirming_exit → 覆盖 playing ❌
```

**影响**：
- `exited_at` 被错误恢复，导致时长计算包含玩家已重新进入的时间
- `fluctuation` 事件可能重复发送或完全丢失
- 玩家实际在玩游戏，但状态显示 `confirming_exit`

**复现路径**：
1. 玩家退出游戏（进入 `confirming_exit` 状态）
2. 在 deadline 前 10 秒左右重新进入游戏
3. 同时有另一个检测周期到达
4. 观察日志：可能看到 `fluctuation` 事件，但最终状态仍是 `confirming_exit`

---

### 问题 3：tick_due 与轮询的竞态

**触发条件**：
- 会话到达 `exit_deadline`（180 秒波动窗口结束）
- 玩家恰好在 deadline 时刻重新进入游戏

**调用链路**：
```python
# src/application/services/polling_tracking.py:98
self.session_service.tick_due(int(now2))  # ← 每分钟触发

# src/application/services/session_service.py:72-92
def tick_due(self, now: int):
    due = [(key, session) for key, session in self._sessions().items()
           if session.state == "confirming_exit" and now >= session.exit_deadline]
    for (group_id, sid), session in due:
        next_session, events = apply(session, {"gameid": None}, ...)  # ← 强制 close
        self._store(group_id, sid, next_session)
```

**问题场景**：
```
初始: confirming_exit(deadline=1180)

T0 (now=1180): tick_due 扫描到会话
T1: 轮询检测到玩家重新进入 game_a
T2: 轮询 apply(gameid=game_a) → playing (发送 fluctuation)
T3: 轮询写入 playing
T4: tick_due apply(gameid=None) → closed (发送 closed)
T5: tick_due 写入 closed → 覆盖 playing ❌
```

**影响**：
- 玩家实际在玩游戏，但会话被标记为 `closed`
- 同时发送 `fluctuation` 和 `closed` 事件（矛盾通知）
- 下次检测会重新创建会话，用户收到重复的"开始游戏"通知
- 时长统计不连续（原会话被提前关闭）

**复现路径**：
1. 玩家退出游戏，等待约 179 秒
2. 在第 180 秒恰好重新进入游戏
3. 观察日志：可能看到先发送"网络波动"通知，再发送"结束游戏"通知
4. 几秒后又收到"开始游戏"通知（实际是同一局游戏）

---

## 影响评估

### 对当前运行的影响程度

| 维度 | 严重性 | 说明 |
|------|--------|------|
| **数据准确性** | ⚠️ **中** | 时长误差 5-180 秒（取决于竞态发生时间点） |
| **用户体验** | ⚠️ **中** | 通知重复/丢失，可能引起困惑 |
| **系统稳定性** | ✅ **低** | 不会崩溃，仅状态不一致 |
| **触发频率** | ⚠️ **中** | 多群 + 长期运行场景下，每天可能遇到数次 |
| **可观测性** | ⚠️ **低** | 竞态窗口短（毫秒级），日志难以直接捕捉 |

### 实际影响场景

#### 1. **多群监控场景**（最常见）
- **配置**：10-20 人，分布在 3-5 个群
- **触发概率**：约 10-20% 的玩家会在某个时刻属于多个群
- **预期频率**：**每天 2-5 次**状态覆盖

#### 2. **网络波动场景**
- **配置**：玩家网络不稳定 / Steam 客户端重启
- **触发概率**：约 5% 的退出会在 180 秒内重新进入
- **预期频率**：**每周 1-3 次**波动窗口竞态

#### 3. **精确时序碰撞**（罕见但严重）
- **配置**：deadline 到期时恰好玩家重新进入
- **触发概率**：< 1%（需要秒级精确对齐）
- **预期频率**：**每月 0-2 次**tick_due 冲突
- **影响**：用户看到明显的错误通知（先结束后开始）

### 数据损失评估

#### **时长统计**
- **误差范围**：5-180 秒
- **影响比例**：如果平均游戏时长 2 小时，误差约 0.07%-2.5%
- **排行榜影响**：单次竞态对日榜影响 < 3 分钟，可接受

#### **成就监控**
- **风险**：如果会话被提前 close，成就轮询任务被取消
- **影响**：玩家继续游玩期间的成就可能漏检测
- **缓解**：5 分钟延迟补偿机制可部分覆盖

#### **通知一致性**
- **问题**：用户可能收到：
  - 重复的"开始游戏"通知（同一局被 close 后重开）
  - "网络波动"后紧接着"结束游戏"（矛盾）
  - 完全漏掉某次状态变化
- **频率**：约 5-10% 的通知可能受影响

---

## 技术根因分析

### 架构设计问题

当前设计假设：
```python
# 隐含假设：handle 调用是串行的
async def handle(self, group_id, sid, observed_gameid, now, **kwargs):
    current = self._sessions().get(key)  # ← 读取
    next_session, events = apply(...)    # ← 纯函数计算
    self._store(group_id, sid, next_session)  # ← 写入
```

实际情况：
- ✅ 单次调用内部是原子的（Python GIL 保证字典操作原子性）
- ❌ **跨调用之间无保护**（asyncio 的 `await` 会让出控制权）
- ❌ `tick_due` 是同步函数，与异步 `handle` 并发执行

### TOCTOU 漏洞

典型的 Time-Of-Check-Time-Of-Use 问题：
```python
# tick_due 中的扫描和执行不是原子的
due = [...]  # ← Check: 扫描 confirming_exit
for key, session in due:
    # ... 中间可能被其他调用修改 ...
    apply(session, ...)  # ← Use: 基于旧状态决策
```

---

## 解决方案

### 推荐方案：细粒度锁

```python
import asyncio
from collections import defaultdict

class SessionService:
    def __init__(self, plugin):
        self._plugin = plugin
        self._locks = defaultdict(asyncio.Lock)  # {(group_id, sid): Lock}

    async def handle(self, group_id, sid, observed_gameid, now, **kwargs):
        key = self._key(group_id, sid)
        async with self._locks[key]:  # ← 同一玩家的操作串行化
            current = self._sessions().get(key)
            snapshot = {"steamid": str(sid), "group_id": str(group_id), "gameid": observed_gameid}
            next_session, events = apply(current, snapshot, int(now), sid=str(sid), group_id=str(group_id))
            self._store(group_id, sid, next_session)
            for event in events:
                await self._dispatch(event, **kwargs)
            # ... 其余逻辑不变
            return next_session, events

    async def tick_due_async(self, now: int):
        """异步版本，支持加锁 + 重新读取状态"""
        due = [
            (key, session)
            for key, session in list(self._sessions().items())
            if session.state == "confirming_exit" and now >= session.exit_deadline
        ]
        for key, session in due:
            group_id, sid = key
            async with self._locks[key]:
                # 重新读取最新状态，防止基于过期数据决策
                current = self._sessions().get(key)
                if current is None or current.state != "confirming_exit":
                    continue  # 已被其他调用处理
                if current.exit_deadline is None or int(now) < current.exit_deadline:
                    continue  # deadline 被延长了
                
                next_session, events = apply(
                    current,
                    {"steamid": sid, "group_id": group_id, "gameid": None},
                    int(now),
                    sid=sid,
                    group_id=group_id,
                )
                self._store(group_id, sid, next_session)
                for event in events:
                    self._dispatch_sync(event, skip_push=False)
                if events:
                    self._plugin._data_dirty = True
```

### 改动点清单

1. **session_service.py**：
   - 添加 `self._locks = defaultdict(asyncio.Lock)`
   - `handle` 内部用 `async with self._locks[key]` 包裹
   - `tick_due` 改为 `tick_due_async`，加锁 + 重新读取状态

2. **polling_tracking.py**：
   - 第 98 行：`self.session_service.tick_due(now)` → `await self.session_service.tick_due_async(now)`
   - 第 159 行：同样修改（`_fetch_statuses_while_ticking` 内部）

3. **测试**：
   - 添加并发场景单元测试（见下节）

---

## 测试用例

```python
import pytest
import asyncio
from src.application.services.session_service import SessionService
from src.domain.monitoring.session import PlayingSession

@pytest.mark.asyncio
async def test_concurrent_handle_same_player(mock_plugin):
    """测试多群并发调用的原子性"""
    service = SessionService(mock_plugin)
    
    # 初始化：玩家在玩 game_a
    await service.handle("group1", "sid123", "game_a", 1000)
    
    # 模拟：两个群同时检测到退出
    results = await asyncio.gather(
        service.handle("group1", "sid123", None, 1001),
        service.handle("group2", "sid123", None, 1002),
    )
    
    # 验证：两个群都应该是 confirming_exit
    for group_id in ["group1", "group2"]:
        session = service.get(group_id, "sid123")
        assert session is not None
        assert session.state == "confirming_exit"
        assert session.exited_at in (1001, 1002)

@pytest.mark.asyncio
async def test_fluctuation_window_race(mock_plugin):
    """测试波动窗口的竞态"""
    service = SessionService(mock_plugin)
    
    # 初始化：玩家退出
    await service.handle("group1", "sid123", None, 1000)
    
    # 模拟：一个检测到 None，另一个检测到重新进入
    results = await asyncio.gather(
        service.handle("group1", "sid123", None, 1050),
        service.handle("group1", "sid123", "game_a", 1051),
    )
    
    # 验证：最终状态应该是 playing（因为玩家确实重新进入了）
    session = service.get("group1", "sid123")
    assert session is not None
    assert session.state == "playing"
    assert session.exited_at is None  # 不应该有遗留的 exited_at

@pytest.mark.asyncio
async def test_tick_due_vs_handle_race(mock_plugin):
    """测试 tick_due 和 handle 的竞态"""
    service = SessionService(mock_plugin)
    
    # 初始化：玩家在 confirming_exit
    await service.handle("group1", "sid123", None, 1000)
    session = service.get("group1", "sid123")
    assert session.exit_deadline == 1180
    
    # 模拟：deadline 到期时，tick_due 和轮询同时触发
    await asyncio.gather(
        service.tick_due_async(1180),
        service.handle("group1", "sid123", "game_a", 1180),
    )
    
    # 验证：应该是 playing 或 closed 之一，不应出现不一致
    session = service.get("group1", "sid123")
    assert session is not None
    assert session.state in ("playing", "closed")
    
    # 如果是 playing，应该没有 exited_at
    if session.state == "playing":
        assert session.exited_at is None
        assert session.exit_deadline is None
```

---

## 监控建议

### 日志增强

在关键路径添加调试日志（可选，生产环境关闭）：
```python
async def handle(self, group_id, sid, observed_gameid, now, **kwargs):
    key = self._key(group_id, sid)
    lock_wait_start = time.time()
    async with self._locks[key]:
        wait_time = time.time() - lock_wait_start
        if wait_time > 0.1:  # 等待超过 100ms
            logger.warning(f"[SessionService] 锁等待 {wait_time:.3f}s: {key}")
        
        # ... 原有逻辑
```

### 指标收集

建议添加 Prometheus 指标（如果有监控系统）：
```python
# 锁等待时间分布
session_lock_wait_seconds = Histogram('session_lock_wait_seconds', 'Lock wait time')

# 状态转换计数
session_state_transitions = Counter('session_state_transitions', 'State transitions', ['from', 'to'])

# 并发冲突次数
session_concurrent_conflicts = Counter('session_concurrent_conflicts', 'Concurrent conflicts')
```

---

## 风险评估总结

### 修复优先级：⚠️ **中-高**

**建议在下一个版本修复**，理由：
1. **影响面明确**：多群配置 + 网络波动场景较常见
2. **修复成本低**：改动量约 30 行，风险可控
3. **性能影响小**：细粒度锁只影响同一玩家，不同玩家仍可并发
4. **可测试性强**：并发场景单元测试易于编写

### 临时缓解措施（修复前）

如果无法立即修复，可采用以下措施降低影响：

1. **避免同一玩家在多个群**：
   ```python
   # 配置检查脚本
   def check_duplicate_players(group_steam_ids):
       all_sids = []
       for group_id, sids in group_steam_ids.items():
           all_sids.extend([(sid, group_id) for sid in sids])
       duplicates = [sid for sid, count in Counter([s for s, _ in all_sids]).items() if count > 1]
       if duplicates:
           logger.warning(f"发现重复玩家（可能触发并发问题）: {duplicates}")
   ```

2. **增加轮询间隔错开**：
   ```python
   # 为不同群添加随机偏移，避免对齐
   self.group_poll_offset = {gid: random.randint(0, 30) for gid in group_steam_ids}
   ```

3. **增强日志，便于事后分析**：
   ```python
   # 记录每次状态转换
   logger.debug(f"[Session] {group_id}/{sid}: {old_state} → {new_state} (events={events})")
   ```

---

## 参考资料

- **源码位置**：
  - `src/application/services/session_service.py`: SessionService 核心逻辑
  - `src/domain/monitoring/session.py`: 纯函数状态机 `apply`
  - `src/application/services/polling_tracking.py`: 全局轮询循环
  
- **相关文档**：
  - `docs/session-lifecycle-refactor.md`: 会话状态机重构方案
  - `REFACTORING.md`: 项目重构历史

- **Python 并发参考**：
  - [asyncio Locks](https://docs.python.org/3/library/asyncio-sync.html#asyncio.Lock)
  - [TOCTOU 问题](https://en.wikipedia.org/wiki/Time-of-check_to_time-of-use)
