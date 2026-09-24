# 主群监控状态晋升机制

## 问题背景

当前系统采用"主群监控 + 分发路由"架构:

- **主群监控** (`group_steam_ids`): 账号首次绑定的群作为主监控群,负责轮询该账号的 Steam 状态
- **分发路由** (`push_groups`): 同一账号在其他群绑定时,记录为分发路由,仅接收主群监控到的状态推送,不重复轮询

### 原有缺陷

删除主群监控关系时,会直接清除 `group_steam_ids` 中的主绑定记录和所有 `push_groups[steam_id]`,导致:

1. 所有依赖该主群的分发路由群失效
2. 这些分发路由群无法继续接收推送
3. 数据完全丢失,需要用户重新绑定

## 解决方案: 状态晋升机制

### 核心思路

删除主群监控关系时,若该账号还有分发路由群,自动将其中一个晋升为新的主群监控,保证监控链路不中断。

### 实现逻辑

`MonitorAdminService.remove_player()` 方法分三种场景处理:

#### 场景 1: 删除分发路由群

仅移除自身路由,保留主群监控,行为不变。

```python
if str(group_id) != str(direct_owner):
    # 从 push_groups[steam_id] 移除当前群
    targets[:] = [target for target in targets if str(target) != str(group_id)]
    return GroupMutationResult(True, "removed push route")
```

#### 场景 2: 删除主群监控且存在分发路由群 ⭐

执行状态晋升:

1. **选举新主群**: 优先选择已启用监控 (`running_groups`) 的分发路由群,否则取第一个
2. **迁移主监控关系**: 将新主群加入 `group_steam_ids[new_primary]`,从 `push_groups[steam_id]` 移除
3. **迁移状态数据**:
   - `group_last_states`: 玩家最后状态
   - `group_last_quit_times`: 游戏退出时间
   - `next_poll_time`: 下次轮询时间
4. **清理原主群**: 若原主群已空,停止其监控轮询并清理相关状态

```python
if targets:
    new_primary = self._select_promotion_candidate(targets)
    self.groups.setdefault(new_primary, []).append(steam_id)
    targets.remove(new_primary)
    self._migrate_state_data(old_group, new_primary, steam_id)
    # ... 清理原主群 ...
    return GroupMutationResult(True, f"removed primary monitor, promoted {new_primary} as new primary")
```

#### 场景 3: 删除主群监控且无分发路由群

执行完全删除,清空所有数据,行为不变。

### 晋升优先级

`_select_promotion_candidate()` 方法实现选举逻辑:

```python
def _select_promotion_candidate(self, targets: list) -> str:
    running_groups = getattr(self._plugin, 'running_groups', set())
    for target in targets:
        if target in running_groups:
            return target  # 优先选择已启用监控的群
    return targets[0]  # 否则选择第一个
```

### 状态迁移

`_migrate_state_data()` 方法负责数据迁移:

```python
def _migrate_state_data(self, old_group: str, new_group: str, steam_id: str) -> None:
    state = self._state
    
    # 迁移玩家状态
    if old_group in state.group_last_states and steam_id in state.group_last_states[old_group]:
        old_state = state.group_last_states[old_group][steam_id]
        state.group_last_states.setdefault(new_group, {})[steam_id] = old_state
    
    # 迁移退出时间
    if old_group in state.group_last_quit_times and steam_id in state.group_last_quit_times[old_group]:
        quit_times = state.group_last_quit_times[old_group][steam_id]
        state.group_last_quit_times.setdefault(new_group, {})[steam_id] = quit_times
    
    # 迁移轮询时间
    if old_group in state.next_poll_time and steam_id in state.next_poll_time[old_group]:
        poll_time = state.next_poll_time[old_group][steam_id]
        state.next_poll_time.setdefault(new_group, {})[steam_id] = poll_time
```

## 测试覆盖

测试文件: `tests/unit/test_monitor_admin_promotion.py`

### 核心场景

- ✅ 单个分发路由群晋升为新主群
- ✅ 多个分发路由群时晋升第一个,其余保留
- ✅ 优先晋升已启用监控的分发路由群
- ✅ 无分发路由群时执行完全删除
- ✅ 删除分发路由群不触发晋升
- ✅ 迁移所有状态数据 (`group_last_states`, `group_last_quit_times`, `next_poll_time`)
- ✅ 原主群为空时清理其监控状态
- ✅ 原主群还有其他玩家时保留该群

## 效果对比

### 修复前

```
群A 监控 sid1
群B 是 sid1 的分发路由

删除群A的 sid1 → 群B 失去主群,无法接收推送 ❌
```

### 修复后

```
群A 监控 sid1
群B 是 sid1 的分发路由

删除群A的 sid1 → 群B 自动晋升为主群,继续监控 ✅
```

## 兼容性

- 不影响现有的添加/查询逻辑
- 删除分发路由群的行为保持不变
- 完全删除场景保持不变
- 仅在删除主群且存在分发路由时触发新逻辑

## 日志输出

状态晋升会输出日志便于追踪:

```
[状态晋升] SteamID 76561198000000001 主群 111 删除,晋升 222 为新主群
[状态晋升] 迁移 group_last_states: 111[76561198000000001] -> 222[76561198000000001]
[状态晋升] 迁移 group_last_quit_times: 111[76561198000000001] -> 222[76561198000000001]
[状态晋升] 迁移 next_poll_time: 111[76561198000000001] -> 222[76561198000000001]
```

## 后续优化建议

1. **WebUI 显示优化**: 在群聊管理界面明确标识"主监控"和"分发路由"状态
2. **手动指定晋升目标**: 允许用户在删除主群时手动选择晋升哪个分发路由群
3. **晋升通知**: 向新主群发送通知,告知其已被晋升为主监控群
4. **状态检查工具**: 提供命令检查账号的主群和分发路由状态

## 相关文档

- 架构图: `docs/architecture/code-graph.svg`
- 逻辑图: `docs/architecture/monitor-routing-logic.svg`
- 测试文件: `tests/unit/test_monitor_admin_promotion.py`
- 实现代码: `src/application/services/monitor_admin.py`
