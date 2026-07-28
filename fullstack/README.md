# Ontology Lab · 真前后端 + 真数据库

仓库根目录那个 `index.html` 是**离线演练场**：零安装，但所有数据在内存里，刷新即重置。

这个目录是它的**全栈版**：真实的电商业务表、真实的外键、真实的事务和乐观锁、
真实的权限检查。它补上了演练场缺的三块 —— **数据源映射**、**权限**、**并发**。

---

## 在 Windows 上启动

需要 **Python 3.10+**（`python --version` 确认）。数据库是 SQLite，不用另外装。

打开 PowerShell 或 cmd，`cd` 到这个 `fullstack` 目录，然后：

### PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m server.initdb
python -m uvicorn server.main:app --reload --port 8000
```

> 如果 `Activate.ps1` 报"禁止运行脚本"，先执行一次：
> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`

### cmd

```bat
python -m venv .venv
.venv\Scripts\activate.bat
pip install -r requirements.txt
python -m server.initdb
python -m uvicorn server.main:app --reload --port 8000
```

然后浏览器打开 **http://127.0.0.1:8000**

想重置数据，再跑一次 `python -m server.initdb` 就行（会删掉旧库重建）。

### 常见问题

| 现象 | 原因与处理 |
|---|---|
| `ModuleNotFoundError: No module named 'server'` | 没在 `fullstack` 目录下运行。`cd` 进来再执行 |
| 页面显示"连不上后端" | 忘了先跑 `python -m server.initdb` |
| 端口被占用 | 换一个：`--port 8001` |
| 中文乱码 | 代码里所有文件读写都显式指定了 UTF-8，不该出现。如果仍有问题请把报错发我 |

---

## 三层架构

### 第 1 层：业务表（假装是"源系统"）

```
buyer(buyer_id, full_name, phone_no, member_lvl, city_name)
product(sku, prod_name, unit_price, stock_qty, row_version)
orders(order_no, buyer_id→buyer, ord_status, placed_at, ship_to_city, carrier_cd, row_version)
order_line(line_no, order_no→orders, sku→product, qty, unit_price, subtotal, row_version)
code_member_level(code, label)
```

这些表是**故意写脏的**，映射层才有活干：

- `member_lvl` 存码值 `01/02/03`，本体层要显示成「普通/银卡/金卡」
- `unit_price`、`subtotal` 以**分**为单位，本体层以元显示
- 列名是缩写：`full_name`、`prod_name`、`ord_status`

### 第 2 层：本体元数据（含映射）

`object_type` / `object_property` / `link_type` / `action_type` 四张表。

**最关键的一点**：`orders.buyer_id`、`order_line.order_no`、`order_line.sku`
这三个外键列，**没有**出现在 `object_property` 里。它们不是属性，是**链接**。

这是关系模型到本体最核心的一次转换，界面上「Order 的映射」那一屏会明确列出来。

### 第 3 层：写入与审计

Action 在**一个事务**里执行：校验 → 生成编辑 → 落 SQL → 提交。
任何一步失败整体回滚，成功和失败都写进 `action_log`。

---

## 这个版本能看到、而演练场看不到的东西

### SQL 面板

右下角。每次点击之后展开，能看到这次请求真正跑了什么。
比如从订单点开「下单买家」：

```sql
SELECT f.* FROM buyer f JOIN orders t ON t.buyer_id = f.buyer_id WHERE t.order_no = ?
```

「沿着链接走」不是比喻，底下就是 JOIN。读本体元数据的查询单独折叠，不干扰阅读。

### 建立链接 = 写外键列

跑一次「加购商品」，编辑预览里的两条 LINK 落到 SQL 就是：

```sql
UPDATE order_line SET order_no = ? WHERE line_no = ?
UPDATE order_line SET sku      = ? WHERE line_no = ?
```

所以**基数由表结构天然守住** —— `orders.buyer_id` 只能装一个值，
一张订单就只能有一个买家，不需要额外的约束代码。

### 权限

顶栏切换用户（没有真实登录，走 `X-Actor` 头）：

| 用户 | 角色 | 能跑 |
|---|---|---|
| 小张 | 客服 | 下单、加购、删除订单行、支付、取消 |
| 老王 | 仓管 | 发货、确认收货、补货 |
| 管理员 | 管理员 | 全部 + 改本体 |

用小张的身份点「发货」，按钮是灰的；硬调 API 返回 403，并且这次拒绝也会进审计日志。

### 并发（乐观锁）

**开两个浏览器标签页**，都切到老王，都选「补货」→ HUB-C8 → 数量 10，
然后尽量同时点提交：

- 一个成功，库存 +10
- 另一个弹出红色横幅：`Product HUB-C8 已被他人修改（你读到的版本已过期），本次提交整体回滚`

没有这个检查，两次 `+10` 都基于同一个旧库存计算，结果只涨 10 —— 另一个人的补货
就这么**静默消失**了。这就是"丢失更新"，也是内存版根本演示不了的东西。

顺带一提：两个人同时**发货**得到的是 422 而不是 409 —— 因为等第二个请求拿到写锁时，
订单已经是「已发货」，**事务内重新校验**先一步拦住了它，报错信息也更准确。
乐观锁是那道兜底防线，专治规则依然满足、但读到的值已过期的情况。

### 三种类型怎么加（附一条能跑通的练习）

数据库里有一张 **`shipment` 表，初始本体故意没有绑定它** —— 专门留给你走一遍这条路径。
用管理员身份进建模模式：

**① 加对象类型 = 绑定一张表。** 左栏「对象类型」→ `+ 绑定表` → 选 `shipment`。
表单会先告诉你每一列会怎么处置：

```
tracking_no  → 属性 trackingNo
order_no     → 跳过：这是指向 orders 的外键，应该建成链接类型
carrier_cd   → 属性 carrierCd
ship_status  → 属性 shipStatus
eta          → 属性 eta
row_version  → 跳过：乐观锁用
```

**外键列不会变成属性** —— 关系模型到本体最核心的一次转换，在这一步看得最清楚。

**② 加链接类型 = 挑一个真实外键。** 左栏「链接类型」→ `+ 新建`。
下拉里只列 `PRAGMA foreign_key_list` 查出来的**真外键**，此时会出现 `shipment.order_no → orders`。
凭空写一个列名会被拒：

> shipment.made_up_col 不是一个外键。链接必须由真实存在的外键实现 ——
> 该表上的外键有：['order_no']

**③ 加操作类型 = 写一段声明式 JSON。** 左栏「操作类型」→ `+ 新建`。
它是**纯元数据**，不依赖任何表结构改动，存进 `action_type` 表立刻就能执行。
换成老王（仓管）就能在运行模式里跑它，换成小张（客服）会 403。

三者的**成本是递增的**：

| | 前提 | 成本 |
|---|---|---|
| 操作类型 | 无 | 最低，随时能加 |
| 链接类型 | 得有一个真实外键 | 中等；没有外键就得先在管道里算出关联 |
| 对象类型 | 得有一张真实的表 | 最高，往往牵动数据工程 |

**解除绑定不删数据**：被链接或操作引用时会直接拒绝（409），解除后业务表和行都还在，
只是本体不再暴露它。

### 加属性的真实成本

建模模式 → 选 Order → 属性映射区，新增属性时只能从**已存在的列**里挑。
试着映射一个不存在的列，会得到：

> 表 orders 上没有 discount_amt 这一列。本体只能映射已存在的列 ——
> 想要新字段，得先改 schema.sql 建表。

演练场里「一键加属性、所有实例自动补默认值」掩盖掉的，就是这个成本。

同理，**解除属性映射不会删数据**：业务表和那一列都还在，只是本体不再暴露它。

---

## 目录

```
db/
  schema.sql          三层建表
  seed_business.sql   电商样例数据（含一张故意不绑定的 shipment 表，留给你练手）
  seed_ontology.sql   本体定义 + 映射 + 9 个 Action + 3 个用户
  ontology.db         运行 initdb 后生成（已被 .gitignore 忽略）
server/
  db.py               连接、事务、SQL 追踪
  ontology.py         读元数据，构建映射
  query.py            对象查询、链接遍历（生成 JOIN）
  actions.py          Action 解释器 + 事务 + 乐观锁
  permissions.py      角色检查
  main.py             FastAPI 路由
  initdb.py           建库脚本
web/
  index.html  app.js  styles.css      样式沿用离线演练场
tests/
  test_query.py  test_actions.py  test_api.py
```

## API

自带文档页：**http://127.0.0.1:8000/api/docs**

```
GET    /api/ontology                          元数据 + 映射
GET    /api/tables                            数据库里有哪些表、列、外键
GET    /api/objects/{type}                    列表
GET    /api/objects/{type}/{pk}               详情 + 关联对象（含各自的 SQL）
GET    /api/graph                             图谱节点与边
GET    /api/actions                           操作类型 + 当前用户能否执行
POST   /api/actions/{id}/preview              校验 + 编辑预览（只读）
POST   /api/actions/{id}/apply                事务执行 → 200/403/409/422
GET    /api/audit                             审计日志
POST   /api/ontology/object-types             绑定一张已存在的表（仅管理员）
PATCH  /api/ontology/object-types/{name}      改显示名/标题属性/配色
DELETE /api/ontology/object-types/{name}      解除绑定（不删数据，被引用时 409）
POST   /api/ontology/properties/{type}        映射一个已存在的列
DELETE /api/ontology/properties/{type}/{prop} 解除映射（不删数据）
POST   /api/ontology/link-types               新建链接（必须指向真实外键）
DELETE /api/ontology/link-types/{id}          解除链接映射
PUT    /api/ontology/action-types/{id}        新建或保存操作类型
DELETE /api/ontology/action-types/{id}        删除操作类型
```

每个响应都带 `_sql` 字段，就是右下角面板的数据来源。

## 跑测试

```powershell
python -m pytest tests -q
```

56 个用例，覆盖映射与值转换、链接遍历生成的 SQL、校验拦截、事务原子性、
乐观锁冲突、权限 403、真线程并发，以及三种类型的创建路径（绑定表时外键被跳过、
链接必须有真外键、新建的操作类型立刻可执行、被引用时不许解除绑定）。

---

## 明确不做的部分

- **N:N 链接**（中间表实现）—— 这个电商模型不需要，订单行已经升格成对象类型了。
  `link_type` 表留了 `join_kind` 扩展位，代码里只实现了外键式链接。
- **真实认证** —— 用户切换是个下拉框，不是登录。
- **数据库迁移工具** —— 改表结构靠改 `schema.sql` 重新 `initdb`。
- **派生属性**（订单总额自动聚合）—— 仍然不存。存一个会算错的字段比不存更糟。
- **数据管道** —— 业务表由 seed 灌入，没有真实同步链路。
- **可视化的 Action 构建器** —— 全栈版里操作类型以声明式 JSON 展示和编辑；
  拖拽式的规则/编辑构建器在离线演练场（仓库根目录的 `index.html`）里。
- **改表结构** —— 界面只能改本体的映射，改不了业务表。加列、建表要动
  `db/schema.sql` 再重新 `initdb`。这是有意的：真实系统里改表也不是本体工具的事。

## 和离线演练场的关系

两者**故意保持独立**，用途不同：

| | 离线演练场 `/index.html` | 全栈版 `/fullstack` |
|---|---|---|
| 启动 | 双击打开 | 装 Python、建库、起服务 |
| 数据 | 内存，刷新即重置 | SQLite 文件，真持久化 |
| 建模 | 可以凭空造对象类型 | 必须绑定一张真实的表 |
| Action 构建 | 可视化表单 | 声明式 JSON |
| 撤销 | 有（撤销上一步） | 没有，只有补偿性 Action |
| 权限 / 并发 / SQL | 没有 | 有 |

先用演练场理解概念，再用全栈版看它在真实系统里是怎么落地的。
