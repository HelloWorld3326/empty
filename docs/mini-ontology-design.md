# 简易 Ontology 系统设计文档（v1.0）

> **本文档的用途**：交给 AI agent 或开发者，据此从零实现一套可运行的简易 Ontology 系统。
> 文档自包含：不依赖任何外部代码库，读完本文即可完整实现。
>
> **设计蓝本**：Palantir Foundry Ontology 的核心思想（对象 + 链接 + 动作 + 提交校验），
> 做教学级最小实现，并通过 MCP 暴露给 AI agent 使用。

---

## 1. 系统目标

把底层数据表翻译成**业务语义层**，让人和 AI agent 用"业务语言"安全地读写数据：

1. **读**：agent 不写 SQL/JOIN，通过"对象 + 链接"查询和遍历业务数据；
2. **写**：agent 不直接改数据，只能调用预定义的"动作"，动作自带业务校验和审计；
3. **接入**：所有能力通过 MCP (Model Context Protocol) 暴露为标准工具，任何 MCP 客户端（Claude Desktop、nexent 等 agent 平台）可直接调用。

### 非目标（v1 明确不做）

- 不做持久化数据库（数据在内存中，进程结束即失；源数据来自 CSV）
- 不做用户/权限体系（仅在审计中记录调用者字符串）
- 不做多对多链接（只实现外键式的一对多/多对一）
- 不做并发控制、事务回滚

---

## 2. 核心概念与术语

| 术语 | 定义 | 类比数据库 | 类比 Palantir |
|---|---|---|---|
| 后备数据集 (Backing Dataset) | 提供原始数据的 CSV 文件 | 一张表 | Dataset |
| 对象类型 (Object Type) | 一类业务实体的模式定义 | 表的 schema | Object Type |
| 对象 (Object) | 一个具体的业务实体 | 一行 | Object |
| 属性 (Property) | 对象的一个字段，有类型 | 一列 | Property |
| 主键 (Primary Key) | 唯一标识对象的属性 | 主键列 | Primary Key |
| 标题键 (Title Key) | 对象的人类可读显示名 | — | Title Key |
| 链接类型 (Link Type) | 两个对象类型之间的关系 | 预定义的 JOIN | Link Type |
| 动作类型 (Action Type) | 受控的写操作：参数+校验+规则 | 存储过程+触发器约束 | Action Type |
| 提交校验 (Submission Criteria) | 动作执行前必须全部通过的条件 | CHECK 约束/业务规则 | Submission Criteria |
| 编辑器 (Ontology Editor) | 动作规则改数据的唯一通道，记录审计 | — | Ontology edit rules |

**两条铁律**（实现时必须保证）：

1. **读写分离**：查询接口永远不改数据；改数据只能通过"执行动作"。
2. **校验前置**：任何一条提交校验不通过，动作整体拒绝，数据分毫不动。

---

## 3. 总体架构

```
┌─────────────────────────────────────────────────┐
│  MCP 工具层  mcp_server.py                       │
│  （把下层能力包装成 6 个 MCP 工具，agent 调用）      │
├─────────────────────────────────────────────────┤
│  领域定义层  ontology/order_ontology.py           │
│  （具体业务：Customer/Order 对象、链接、2 个动作）   │
├─────────────────────────────────────────────────┤
│  通用引擎层  ontology/core.py                     │
│  （ObjectType/LinkType/ActionType 等通用机制）     │
├─────────────────────────────────────────────────┤
│  数据层  data/*.csv                              │
│  （后备数据集，启动时加载进内存）                    │
└─────────────────────────────────────────────────┘
```

**分层原则**：引擎层不包含任何"订单"字样（完全通用）；领域层只做声明式定义，不含机制代码。换一个业务领域时只需替换领域层和数据层。

### 文件结构

```
.
├── data/
│   ├── customers.csv          # 客户数据
│   └── orders.csv             # 订单数据
├── ontology/
│   ├── __init__.py            # 包导出
│   ├── core.py                # 通用引擎（≈300 行）
│   └── order_ontology.py      # 订单领域定义（≈150 行）
├── demo.py                    # 命令行演示脚本（验收用）
├── mcp_server.py              # MCP server（≈130 行）
├── requirements.txt
└── README.md
```

**技术选型**：Python 3.8+；引擎与 demo **零第三方依赖**（仅标准库 csv/dataclasses/datetime）；MCP 层依赖 `mcp[cli]>=1.2.0`（官方 Python SDK，使用其中的 FastMCP）。

---

## 4. 通用引擎层规格（ontology/core.py）

### 4.1 属性类型系统

支持 5 种属性类型，从 CSV 字符串强制转换：

| 类型名 | Python 类型 | 转换规则 |
|---|---|---|
| `string` | str | 原样 |
| `integer` | int | int() |
| `double` | float | float() |
| `boolean` | bool | "1/true/yes/y"（不分大小写）为 True |
| `date` | datetime.date | 按 `YYYY-MM-DD` 解析 |

空字符串/None 转换为 None。提供函数 `_coerce(value, type_name)`。

```python
@dataclass
class Property:
    name: str          # API 名（程序用）
    type: str          # 上表 5 种之一
    title: str = ""    # 显示名，缺省取 name
```

### 4.2 ObjectType 与 Object

```python
@dataclass
class ObjectType:
    name: str                          # 如 "Order"
    primary_key: str                   # 主键属性名
    title_key: str                     # 标题属性名
    properties: Dict[str, Property]    # 属性名 -> Property
    rows: List[Dict[str, Any]]         # 内存中的数据行
    plural: str = ""                   # 复数名，缺省 name+"s"
```

**必须实现的方法**：

| 方法 | 签名 | 行为 |
|---|---|---|
| `from_csv` | `(cls, name, csv_path, primary_key, title_key, properties: List[Property], plural="") -> ObjectType` | 类方法。读 CSV，仅保留 properties 中声明的列，按属性类型转换每个值；构造后**必须校验主键唯一**，重复则抛 `ValueError` |
| `all` | `() -> List[Object]` | 返回全部对象 |
| `get` | `(pk) -> Optional[Object]` | 按主键查单个，不存在返回 None |
| `search` | `(**equals) -> List[Object]` | 按属性等值过滤（AND 语义） |

`Object` 是行的轻量包装：

```python
class Object:
    # 构造: Object(object_type, row_dict)
    pk: Any        # property, 返回主键值
    title: Any     # property, 返回标题值
    def __getitem__(self, prop): ...   # obj["status"]
    def get(self, prop, default=None): ...
    def to_dict(self) -> dict: ...     # 返回行的浅拷贝
    def __repr__(self): ...            # 形如 <Order O1001: 机械键盘>
```

### 4.3 LinkType（外键式链接）

```python
@dataclass
class LinkType:
    name: str            # 正向名，如 "customer"（订单→它的客户）
    source: str          # 源对象类型名，如 "Order"
    target: str          # 目标对象类型名，如 "Customer"
    foreign_key: str     # 源对象上指向目标主键的属性，如 "customer_id"
    reverse_name: str = ""  # 反向名，如 "orders"；缺省 source 小写+"s"
```

语义：`source.foreign_key == target.primary_key`。
- **正向遍历**（多→一）：从一个 Order 沿 `customer` 得到 0 或 1 个 Customer；
- **反向遍历**（一→多）：从一个 Customer 沿 `orders` 得到其全部 Order。

### 4.4 动作系统

```python
@dataclass
class Parameter:
    name: str
    type: str            # 5 种基础类型，或 "object<类型名>" 表示对象引用
    description: str = ""
    required: bool = True

@dataclass
class Criterion:                    # 一条提交校验
    message: str                    # 不通过时给调用方的解释
    predicate: Callable[[ActionContext], bool]

@dataclass
class ActionContext:                # 规则/校验的运行上下文
    params: Dict[str, Any]          # 已解析的参数（对象引用已变成 Object）
    user: str
    ontology: "Ontology"
    def param(self, name): ...

@dataclass
class ActionType:
    name: str
    description: str
    parameters: List[Parameter]
    apply: Callable[[OntologyEditor, ActionContext], None]   # 规则
    submission_criteria: List[Criterion] = field(default_factory=list)
```

**OntologyEditor**——动作改数据的唯一通道，每次编辑追加一条审计记录：

```python
class OntologyEditor:
    edits: List[dict]   # 审计日志
    def modify_object(self, obj, **changes): ...
        # 就地更新行；记 {"op":"modify","type","pk","changes","by"}
    def create_object(self, type_name, **props) -> Object: ...
        # 追加行；追加后重新校验主键唯一；记 {"op":"create",...}
    def delete_object(self, obj): ...
        # 从 rows 移除；记 {"op":"delete","type","pk","by"}
```

**异常与结果**：

```python
class ActionDenied(Exception):
    reasons: List[str]      # 所有未通过的校验信息 / 参数错误

@dataclass
class ActionResult:
    action: str
    edits: List[dict]
    by: str
```

### 4.5 Ontology 门面类

```python
class Ontology:
    # 注册
    def add_object_type(self, ot): ...
    def add_link_type(self, lt): ...
    def add_action(self, at): ...
    # 元数据
    def object_type(self, name) -> ObjectType          # 不存在抛 KeyError
    def object_type_names(self) -> List[str]
    def action(self, name) -> ActionType               # 不存在抛 KeyError
    def action_names(self) -> List[str]
    # 查询
    def objects(self, type_name) -> List[Object]
    def get(self, type_name, pk) -> Optional[Object]
    def search(self, type_name, **equals) -> List[Object]
    def linked(self, obj, link_name) -> List[Object]
    # 执行动作
    def execute_action(self, action_name, params: dict, user="anonymous") -> ActionResult
```

**`linked` 的解析顺序**：先在所有 LinkType 中找"`name==link_name` 且 `source==obj 的类型`"（正向，返回 0/1 个）；找不到再找"`reverse_name==link_name` 且 `target==obj 的类型`"（反向，返回多个）；都没有则抛 `KeyError`。

**`execute_action` 的执行序**（顺序不可改变）：

```
1. 解析参数 _resolve_params:
   - 必填缺失          -> ActionDenied(["缺少必填参数: x"])
   - 基础类型          -> _coerce 转换
   - object<T> 类型    -> 用参数值当主键去 get(T, value)，
                          不存在 -> ActionDenied(["参数 x 指向的 T 不存在: ..."])
2. 依次评估全部 submission_criteria，收集所有不通过的 message
   - 有任何不通过     -> 抛 ActionDenied(全部原因)，数据不动
3. 全部通过           -> 创建 OntologyEditor，调用 action.apply(editor, ctx)
4. 返回 ActionResult(action, editor.edits, user)
```

---

## 5. 领域定义层规格（ontology/order_ontology.py）

提供工厂函数 `build_ontology() -> Ontology`，注册以下内容。

### 5.1 对象类型

**Customer**（后备 `data/customers.csv`，主键 `customer_id`，标题 `name`）

| 属性 | 类型 | 显示名 |
|---|---|---|
| customer_id | string | 客户ID |
| name | string | 姓名 |
| tier | string | 会员等级（gold/silver/bronze）|
| email | string | 邮箱 |

**Order**（后备 `data/orders.csv`，主键 `order_id`，标题 `item_name`）

| 属性 | 类型 | 显示名 |
|---|---|---|
| order_id | string | 订单ID |
| order_number | string | 订单号 |
| customer_id | string | 客户ID（外键）|
| item_name | string | 商品名称 |
| amount | double | 金额 |
| status | string | 状态 |
| created_at | date | 下单日期 |

**状态机约定**：`status ∈ {pending, unshipped, shipped, delivered, cancelled}`；
可取消状态集合 `CANCELLABLE_STATUSES = {"unshipped", "pending"}`。

### 5.2 链接类型

```
LinkType(name="customer", source="Order", target="Customer",
         foreign_key="customer_id", reverse_name="orders")
```

即：`order.customer` → 该订单的客户（1 个）；`customer.orders` → 该客户全部订单（多个）。

### 5.3 动作类型

**动作 1：cancel_order（演示 Modify 规则 + 校验拦截）**

- 描述：取消一笔订单。只有处于可取消状态（未发货/待处理）的订单才允许取消。
- 参数：
  - `order: object<Order>`（必填，传订单 ID）
  - `reason: string`（选填，取消原因）
- 提交校验：
  - `order.status ∈ CANCELLABLE_STATUSES`，否则拒绝，消息：
    "该订单当前状态不允许取消(只有未发货/待处理的订单可取消)。"
- 规则：`modify_object(order, status="cancelled", cancel_reason=reason)`

**动作 2：place_order（演示 Create 规则）**

- 描述：为某个客户下一笔新订单。金额必须为正数。
- 参数：
  - `customer: object<Customer>`（必填，传客户 ID）
  - `item_name: string`（必填）
  - `amount: double`（必填）
- 提交校验：
  - `amount > 0`，否则拒绝，消息："金额必须大于 0。"
- 规则：`create_object("Order", ...)`，其中：
  - `order_id`：自增生成——取现有订单 ID 数字部分最大值 +1，格式 `O####`（如 O1006）
  - `order_number`："ORD-" + ID 数字部分
  - `customer_id` = 客户主键；`status` = "pending"；`created_at` = None

### 5.4 种子数据

`data/customers.csv`：

```csv
customer_id,name,tier,email
C001,张三,gold,zhangsan@example.com
C002,李四,silver,lisi@example.com
C003,王五,bronze,wangwu@example.com
```

`data/orders.csv`：

```csv
order_id,order_number,customer_id,item_name,amount,status,created_at
O1001,ORD-1001,C001,机械键盘,399.00,unshipped,2026-07-08
O1002,ORD-1002,C001,USB-C 数据线,59.00,shipped,2026-07-05
O1003,ORD-1003,C002,4K 显示器,1899.00,unshipped,2026-07-10
O1004,ORD-1004,C003,鼠标垫,29.00,delivered,2026-06-30
O1005,ORD-1005,C001,人体工学椅,1299.00,cancelled,2026-07-01
```

> 种子数据刻意构造了教学场景：张三有"未发货/已发货/已取消"三种订单，
> 可演示"取消成功"与"取消被拦截"两条路径。

---

## 6. MCP 工具层规格（mcp_server.py）

使用官方 Python SDK 的 `FastMCP`，server 名 `"order-ontology"`，stdio 传输。
进程内共享**单个** `build_ontology()` 实例（动作产生的编辑在进程存活期间可见）。

所有工具返回 **JSON 字符串**（`ensure_ascii=False`，date 用 `default=str` 序列化）。

### 工具清单（6 个）

| 工具 | 参数 | 返回 | 对应 Palantir OMCP 能力 |
|---|---|---|---|
| `list_object_types` | 无 | `[{name, primary_key, title_key, properties:{名:类型}, count}]` | 模式发现 |
| `query_objects` | `object_type: str, filters: dict = None` | 匹配对象的 `to_dict()` 数组 | 对象查询 |
| `get_object` | `object_type: str, primary_key: str` | 单个对象 dict；不存在返回 `{"error": ...}` | 按主键取对象 |
| `traverse_link` | `object_type: str, primary_key: str, link_name: str` | 关联对象数组 | 沿链接遍历 |
| `list_actions` | 无 | `[{name, description, parameters:[{name,type,required,description}]}]` | 动作发现 |
| `execute_action` | `action_name: str, parameters: dict, user: str = "agent:mcp"` | 见下 | 执行动作 |

### execute_action 的返回约定

```jsonc
// 成功
{ "status": "ok", "action": "cancel_order", "by": "agent:mcp", "edits": [ ...审计... ] }
// 被校验拒绝（不是异常，正常返回给 agent 让它理解原因）
{ "status": "denied", "reasons": ["该订单当前状态不允许取消(...)。"] }
// 动作名不存在等
{ "status": "error", "message": "..." }
```

**错误处理原则**：MCP 工具内捕获 `ActionDenied`/`KeyError`，转成结构化 JSON 返回，
**不向 MCP 客户端抛异常**——agent 需要读懂拒绝原因并向用户解释。

### 接入方式

- **本地（Claude Desktop / 本地 agent）**：客户端以子进程方式启动 `python mcp_server.py`（stdio）。
  Claude Desktop 配置示例：

```json
{
  "mcpServers": {
    "order-ontology": {
      "command": "python",
      "args": ["/绝对路径/mcp_server.py"]
    }
  }
}
```

- **远程（nexent 等平台）**：将 `mcp.run()` 改为 `mcp.run(transport="streamable-http")`，
  平台侧填该 HTTP URL。

**依赖缺失的友好降级**：`mcp_server.py` 顶部 import 失败时，用 `SystemExit` 打印
安装提示（`pip install "mcp[cli]"`），并说明纯学习可改跑 `demo.py`。

---

## 7. 演示脚本规格（demo.py）

零依赖，`python demo.py` 直接运行。按顺序演示 6 幕（每幕打印分节标题）：

1. **读对象**：列出全部订单（ID、商品、金额、状态）
2. **反向链接**：`search(Customer, name="张三")` → `linked(张三, "orders")` 列出其订单
3. **定位 + 正向链接**：过滤张三 `status=="unshipped"` 的订单；对结果调 `linked(order, "customer")` 反查回客户，验证双向遍历
4. **动作成功**：`execute_action("cancel_order", {order: 该订单pk, reason: "用户主动取消"}, user="agent:nexent")`，打印审计记录，再读该订单确认状态已是 `cancelled`
5. **校验拦截**：对一笔 `shipped` 订单执行 cancel_order，捕获 `ActionDenied` 打印原因；再读确认状态未变
6. **动作创建**：`execute_action("place_order", {customer: 李四pk, item_name: "无线鼠标", amount: 129.0})`，打印审计；确认李四订单数 +1

---

## 8. 验收标准（实现完成后逐条核对）

**引擎正确性**

- [ ] CSV 加载后类型正确：`amount` 是 float，`created_at` 是 date
- [ ] 主键重复的 CSV 在加载时抛 `ValueError`
- [ ] `search` 多条件是 AND 语义
- [ ] `linked` 正反向都可用；未知链接名抛 `KeyError`

**动作语义**

- [ ] 缺必填参数 → `ActionDenied`，数据不变
- [ ] `object<T>` 参数传了不存在的主键 → `ActionDenied`，数据不变
- [ ] 取消 `shipped` 订单 → `ActionDenied`，订单状态保持 `shipped`
- [ ] 取消 `unshipped` 订单 → 成功，状态变 `cancelled`，`edits` 含一条 modify 审计（含操作者）
- [ ] `place_order` 金额 ≤0 → 拒绝；合法 → 新订单 `O1006`、状态 `pending`、审计含 create
- [ ] 校验失败时 `apply` 绝不执行（可在 apply 里埋 print 验证）

**MCP 层**

- [ ] 6 个工具齐全，返回合法 JSON（中文不转义）
- [ ] `execute_action` 被拒时返回 `{"status":"denied", ...}` 而非抛错
- [ ] 端到端：MCP 客户端连上后，能只靠 `list_object_types`/`list_actions` 的自描述完成"帮张三取消未发货订单"任务

**demo**

- [ ] `python demo.py` 无报错跑完 6 幕，输出与第 7 节语义一致

---

## 9. 扩展路线（v2+，供后续演进参考）

| 方向 | 做法 |
|---|---|
| 持久化 | rows 换成 SQLite/Postgres 表；Editor 的三个方法改为 SQL，审计写独立表 |
| 权限 | Criterion 已能拿到 `ctx.user`；引入角色表，加"基于用户"的校验模板 |
| 多对多链接 | LinkType 增加 `join_dataset` 模式（一张主键对表） |
| 复杂查询 | query_objects 增加范围/包含等操作符，或暴露只读 SQL 工具（对齐 Palantir OMCP 的 SQL 工具） |
| 函数动作 | 允许 apply 调用外部函数/服务（对齐 function-backed action） |
| 换领域 | 仅重写领域定义层 + CSV；引擎与 MCP 层零改动 |

---

## 附录：与 Palantir 概念对照速查

| Palantir Foundry | 本系统 |
|---|---|
| Ontology Manager 里"选 backing dataset + 主键 + 标题" | `ObjectType.from_csv(...)` |
| Link Type（外键式） | `LinkType` |
| Action Type = Parameters + Rules + Submission Criteria | `ActionType` = `parameters` + `apply` + `submission_criteria` |
| Ontology edit rules (create/modify/delete object) | `OntologyEditor` 三方法 |
| Ontology MCP (OMCP) | `mcp_server.py` 的 6 个工具 |
