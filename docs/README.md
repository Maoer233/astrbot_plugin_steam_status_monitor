# 文档目录

`self/` 放不上传的本地稿，由 `.gitignore` 忽略。其余按类别归档后可以提交。

仓库根目录的 `README.md`、`REFACTORING.md` 仍是对外入口，不搬进这里。命令层拆分的用户向说明在 `README.md`「模块结构」；复盘在 `REFACTORING.md` 第 10 节。

## 分类

| 目录 | 用途 |
| --- | --- |
| `adr/` | 已拍板的架构决策 |
| `design/` | 方案与设计（会话、字体、日志、模块拆分） |
| `postmortem/` | 问题分析与复盘 |

## ADR

- [删除透传中介并内联单次私有辅助](adr/adr-inline-passthrough-helpers.md)
- [命令层拆分：先抽应用服务，再挪 AstrBot 胶水](adr/adr-command-layer-split.md)

## 设计

- [命令层拆分看法与修正](design/command-layer-split.md)
- [价格查询依赖与排行榜状态持有](design/price-and-ranking-ownership.md)

## 复盘

- [SessionService 并发安全](postmortem/concurrency-safety-issue.md)


