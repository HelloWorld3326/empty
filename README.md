# 迷你 Ontology Demo(订单管理)

一套**麻雀虽小五脏俱全**的本地 Ontology 实现,用来学习 Palantir Foundry
Ontology 的核心思想:把底层数据表,变成业务人员和 AI agent 能用"人话"操作的
**对象 + 链接 + 动作**语义层。

> 背景:Palantir Foundry / AIP Developer Tier 目前对中国区不开放。这个 demo
> 让你**抛开地区限制**,在本地把 Ontology 的概念和用法完整跑通,并且做成了
> **能被 agent(如 nexent)通过 MCP 调用**的形态。

---

## 概念对照表(这才是重点)

| Palantir Foundry 概念 | 本 demo 的实现 | 在哪 |
|---|---|---|
| Backing dataset(后备数据集) | `data/*.csv` | `data/` |
| Object Type(对象类型) | `ObjectType` | `ontology/core.py` |
| Object(对象=一行) | `Object` | `ontology/core.py` |
| Property(属性=一列) | `Property` | `ontology/core.py` |
| Primary Key / Title Key | `primary_key` / `title_key` | `ontology/order_ontology.py` |
| Link Type(链接类型=JOIN) | `LinkType`(外键) | `ontology/order_ontology.py` |
| Action Type(动作类型) | `ActionType` | `ontology/core.py` |
| Submission Criteria(提交校验) | `Criterion` | `ontology/order_ontology.py` |
| Ontology edit rules(增/改/删) | `OntologyEditor` | `ontology/core.py` |
| **Ontology MCP (OMCP)** | `mcp_server.py` | 根目录 |

---

## 文件结构

```
.
├── data/                     # "后备数据集":对象背后的真实数据(CSV)
│   ├── customers.csv
│   └── orders.csv
├── ontology/
│   ├── core.py               # 通用 Ontology 引擎(对象/链接/动作机制)
│   └── order_ontology.py     # 订单 Ontology 的"定义"(= 你在 Ontology Manager 里点的东西)
├── demo.py                   # 一键演示:查询→遍历→执行动作→校验拦截→建对象
├── mcp_server.py             # 把 Ontology 暴露成 MCP 工具(对应 Palantir OMCP)
└── README.md
```

三层结构,正好对应你学到的三个阶段:
**引擎(机制)** → **定义(建对象/链接/动作)** → **使用(agent 通过 MCP 调用)**。

---

## 快速开始

### 1. 跑演示(零依赖,只需 Python 3.8+)

```bash
python demo.py
```

你会看到一条完整业务链路:
1. 列出所有订单(读对象)
2. 找到客户"张三"→ 顺着链接列出他的所有订单(沿关系遍历,不写 JOIN)
3. 定位"未发货"订单
4. 执行 `cancel_order` → 校验通过 → 状态改为 cancelled,并留下**审计记录**
5. 试图取消一笔"已发货"订单 → **被提交校验拦截**,数据分毫不动
6. 执行 `place_order` → 新建一个对象

### 2. 跑 MCP server(接 agent 时用)

```bash
pip install "mcp[cli]"
python mcp_server.py
```

它通过 stdio 暴露这些工具:`list_object_types`、`query_objects`、`get_object`、
`traverse_link`、`list_actions`、`execute_action`。

---

## 怎么接到 nexent(或任意 MCP 平台)

nexent 原生支持"第三方 MCP 服务"。把本 server 挂上去,agent 就能像用任何工具
一样查询对象、执行动作。两种接法:

- **本地 stdio**:让 nexent 以子进程方式启动 `python mcp_server.py`。
- **远程 HTTP**:把 `mcp_server.py` 末尾的 `mcp.run()` 改成
  `mcp.run(transport="streamable-http")`,部署后在 nexent 里填它的 URL。

接上后,你对 agent 说"帮张三把没发货的订单取消了",它的内部动作就是:
`query_objects` 找订单 → `execute_action("cancel_order", ...)` 执行,
而**提交校验保证它不会误取消已发货的单**。这正是 Ontology 让 agent 操作业务
既方便又安全的原因。

---

## 动手改改看(加深理解)

- **加一个对象类型**:在 `data/` 放一张新 CSV,在 `order_ontology.py` 里
  `ObjectType.from_csv(...)` 定义它、选主键和标题。
- **加一条链接**:`LinkType(name=..., source=..., target=..., foreign_key=...)`。
- **加一个动作**:写 `apply` 规则 + `submission_criteria` 校验,`add_action`。
  试着给 `cancel_order` 加一条校验:"只有 gold 会员的订单能免费取消"。

---

## 和真实 Palantir 的关系

这个 demo **刻意做成同构**:你在这里理解的每个概念,在真实 Foundry 里都有
一一对应(见上面的对照表)。将来如果你拿到了 Foundry 环境:

- 这里的 `data/*.csv` → 那里由数据管道产出的 dataset;
- 这里 `order_ontology.py` 里的定义 → 那里在 **Ontology Manager** 里点几下;
- 这里的 `mcp_server.py` → 那里官方的 **Ontology MCP (OMCP)**,自动生成、带权限治理。

概念完全迁移,只是从"自己写"变成"平台托管"。

---

> 说明:这是教学用的最小实现,**不含**持久化数据库、并发、权限体系、
> 多对多链接、函数式动作(function-backed action)等生产特性。它的价值在于:
> 用最少的代码,把 Ontology 的心智模型讲透。
