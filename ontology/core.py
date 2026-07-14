"""
迷你 Ontology 引擎 (mini-ontology engine)
=========================================

这是一个用于**教学**的本地 Ontology 实现，目标是让你理解 Palantir Foundry
Ontology 的三块核心积木在"代码层面"到底是什么。它不依赖任何第三方库。

    Palantir 概念            本文件中的对应实现
    --------------------    ----------------------------------
    Backing dataset          ObjectType.rows   (一张内存里的"表")
    Object Type              ObjectType        (表 -> 业务对象 的映射)
    Object                   Object            (表里的一行 = 一个对象)
    Property                 Property          (表里的一列)
    Primary Key              ObjectType.primary_key
    Title Key                ObjectType.title_key
    Link Type                LinkType          (两个对象类型间的关系 = 一次 JOIN)
    Action Type              ActionType        (参数 + 规则 + 提交校验)
    Submission Criteria      ActionType.submission_criteria
    Ontology edit rules      OntologyEditor    (create / modify / delete)

引擎本身不关心"订单",它是通用的;具体的订单 Ontology 定义在
order_ontology.py 里(相当于你在 Ontology Manager 里点出来的东西)。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Optional


# ---------------------------------------------------------------------------
# 属性类型系统:声明一列是什么类型(对应 Ontology Manager 里给 property 选类型)
# ---------------------------------------------------------------------------

def _coerce(value: Any, type_name: str) -> Any:
    """把 CSV 里读出来的字符串,转换成属性声明的类型。"""
    if value is None or value == "":
        return None
    if type_name == "string":
        return str(value)
    if type_name == "integer":
        return int(value)
    if type_name == "double":
        return float(value)
    if type_name == "boolean":
        return str(value).strip().lower() in ("1", "true", "yes", "y")
    if type_name == "date":
        if isinstance(value, (date, datetime)):
            return value if isinstance(value, date) else value.date()
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    raise ValueError(f"未知的属性类型: {type_name}")


@dataclass
class Property:
    """一个属性 = 底层表里的一列。"""
    name: str            # 属性的 API 名(程序里用)
    type: str            # string / integer / double / boolean / date
    title: str = ""      # 给人看的显示名

    def __post_init__(self) -> None:
        if not self.title:
            self.title = self.name


# ---------------------------------------------------------------------------
# 对象 / 对象类型
# ---------------------------------------------------------------------------

class Object:
    """一个对象 = 底层表里的一行。用 obj["属性名"] 取属性值。"""

    def __init__(self, object_type: "ObjectType", row: Dict[str, Any]) -> None:
        self._type = object_type
        self._row = row

    @property
    def object_type(self) -> "ObjectType":
        return self._type

    @property
    def pk(self) -> Any:
        """主键值:唯一标识这个对象。"""
        return self._row[self._type.primary_key]

    @property
    def title(self) -> Any:
        """标题值:这个对象在平台里显示的名字。"""
        return self._row[self._type.title_key]

    def __getitem__(self, prop: str) -> Any:
        return self._row.get(prop)

    def get(self, prop: str, default: Any = None) -> Any:
        return self._row.get(prop, default)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self._row)

    def __repr__(self) -> str:
        return f"<{self._type.name} {self.pk}: {self.title}>"


@dataclass
class ObjectType:
    """
    对象类型 = 把一张"表"映射成一类业务对象。

    这正是你在 Ontology Manager 里 "Create object type" 那一步做的事:
    选一张 backing dataset(rows) -> 选主键(primary_key) -> 选标题(title_key)
    -> 每一列变成一个 property。
    """
    name: str
    primary_key: str
    title_key: str
    properties: Dict[str, Property] = field(default_factory=dict)
    rows: List[Dict[str, Any]] = field(default_factory=list)
    plural: str = ""

    def __post_init__(self) -> None:
        if not self.plural:
            self.plural = self.name + "s"

    # -- 加载数据(相当于把 dataset 挂到对象类型上) --
    @classmethod
    def from_csv(
        cls,
        name: str,
        csv_path: str,
        primary_key: str,
        title_key: str,
        properties: List[Property],
        plural: str = "",
    ) -> "ObjectType":
        prop_map = {p.name: p for p in properties}
        rows: List[Dict[str, Any]] = []
        with open(csv_path, newline="", encoding="utf-8") as fh:
            for raw in csv.DictReader(fh):
                row = {k: _coerce(v, prop_map[k].type) for k, v in raw.items() if k in prop_map}
                rows.append(row)
        ot = cls(name=name, primary_key=primary_key, title_key=title_key,
                 properties=prop_map, rows=rows, plural=plural)
        ot._validate_primary_key()
        return ot

    def _validate_primary_key(self) -> None:
        seen = set()
        for row in self.rows:
            pk = row.get(self.primary_key)
            if pk in seen:
                raise ValueError(
                    f"对象类型 {self.name} 的主键 {self.primary_key} 有重复值 {pk!r};"
                    f" 主键必须每行唯一。"
                )
            seen.add(pk)

    # -- 查询 API(agent 读数据时用的就是这些) --
    def all(self) -> List[Object]:
        return [Object(self, r) for r in self.rows]

    def get(self, pk: Any) -> Optional[Object]:
        for r in self.rows:
            if r.get(self.primary_key) == pk:
                return Object(self, r)
        return None

    def search(self, **equals: Any) -> List[Object]:
        """按属性等值过滤,例如 search(status="unshipped")。"""
        out = []
        for r in self.rows:
            if all(r.get(k) == v for k, v in equals.items()):
                out.append(Object(self, r))
        return out


# ---------------------------------------------------------------------------
# 链接类型:两个对象类型之间的关系(= 一次预先固化、命名好的 JOIN)
# ---------------------------------------------------------------------------

@dataclass
class LinkType:
    """
    链接类型 = 定义两个对象类型之间的关系。

    这里演示最常见的**外键(foreign key)**方式:源对象的某个属性(foreign_key)
    指向目标对象的主键。例如 Order.customer_id -> Customer 的主键,构成
    "Order --belongs_to--> Customer"(多对一)。

    (many-to-many 会用一张 join 表,原理相同,demo 里先只做外键。)
    """
    name: str                # 正向关系名,如 "customer"
    source: str              # 源对象类型名,如 "Order"
    target: str              # 目标对象类型名,如 "Customer"
    foreign_key: str         # 源对象上指向目标主键的属性,如 "customer_id"
    reverse_name: str = ""   # 反向关系名,如 "orders"

    def __post_init__(self) -> None:
        if not self.reverse_name:
            self.reverse_name = self.source.lower() + "s"


# ---------------------------------------------------------------------------
# 动作类型:参数 + 提交校验 + 规则(唯一被允许的"写"入口)
# ---------------------------------------------------------------------------

@dataclass
class Parameter:
    """动作的一个输入参数。type 可为普通类型,或 object<类型名> 表示对象引用。"""
    name: str
    type: str
    description: str = ""
    required: bool = True


class OntologyEditor:
    """
    动作规则通过它来编辑 Ontology —— 对应 Palantir 的 "Ontology edit rules"
    (create object / modify object / delete object)。每次编辑都会记进审计日志,
    这正是 Action 相比"直接改数据库"更安全的原因:一切可控、可追溯。
    """

    def __init__(self, ontology: "Ontology", user: str) -> None:
        self._onto = ontology
        self._user = user
        self.edits: List[Dict[str, Any]] = []

    def modify_object(self, obj: Object, **changes: Any) -> None:
        for k, v in changes.items():
            obj._row[k] = v
        self.edits.append({"op": "modify", "type": obj.object_type.name,
                           "pk": obj.pk, "changes": changes, "by": self._user})

    def create_object(self, type_name: str, **props: Any) -> Object:
        ot = self._onto.object_type(type_name)
        ot.rows.append(props)
        ot._validate_primary_key()
        self.edits.append({"op": "create", "type": type_name,
                           "pk": props.get(ot.primary_key), "props": props, "by": self._user})
        return Object(ot, props)

    def delete_object(self, obj: Object) -> None:
        ot = obj.object_type
        ot.rows.remove(obj._row)
        self.edits.append({"op": "delete", "type": ot.name, "pk": obj.pk, "by": self._user})


@dataclass
class Criterion:
    """一条提交校验规则:predicate 返回 True 才算通过,否则用 message 说明为什么不行。"""
    message: str
    predicate: Callable[["ActionContext"], bool]


@dataclass
class ActionContext:
    """规则/校验运行时能拿到的上下文:已解析好的参数、发起用户、整个 ontology。"""
    params: Dict[str, Any]
    user: str
    ontology: "Ontology"

    def param(self, name: str) -> Any:
        return self.params.get(name)


@dataclass
class ActionType:
    """
    动作类型 = Palantir 里的 Action Type,由三部分组成:

      1. parameters          —— 输入(有类型;object<T> 是对象引用)
      2. submission_criteria —— 提交校验(全部通过才允许执行,守住业务规则/权限)
      3. apply               —— 规则:把参数变成对 Ontology 的编辑(增/改/删)
    """
    name: str
    description: str
    parameters: List[Parameter]
    apply: Callable[[OntologyEditor, ActionContext], None]
    submission_criteria: List[Criterion] = field(default_factory=list)


class ActionDenied(Exception):
    """提交校验未通过时抛出,reasons 列出所有未满足的条件。"""
    def __init__(self, reasons: List[str]) -> None:
        self.reasons = reasons
        super().__init__("; ".join(reasons))


@dataclass
class ActionResult:
    action: str
    edits: List[Dict[str, Any]]
    by: str


# ---------------------------------------------------------------------------
# Ontology:把上面所有东西装在一起,对外提供查询 + 执行动作两类能力
# ---------------------------------------------------------------------------

class Ontology:
    def __init__(self) -> None:
        self._object_types: Dict[str, ObjectType] = {}
        self._link_types: Dict[str, LinkType] = {}
        self._actions: Dict[str, ActionType] = {}

    # -- 注册(相当于在 Ontology Manager 里定义出这些东西) --
    def add_object_type(self, ot: ObjectType) -> None:
        self._object_types[ot.name] = ot

    def add_link_type(self, lt: LinkType) -> None:
        self._link_types[lt.name] = lt

    def add_action(self, at: ActionType) -> None:
        self._actions[at.name] = at

    # -- 元数据 --
    def object_type(self, name: str) -> ObjectType:
        if name not in self._object_types:
            raise KeyError(f"没有这个对象类型: {name}")
        return self._object_types[name]

    def object_type_names(self) -> List[str]:
        return list(self._object_types)

    def action_names(self) -> List[str]:
        return list(self._actions)

    def action(self, name: str) -> ActionType:
        if name not in self._actions:
            raise KeyError(f"没有这个动作: {name}")
        return self._actions[name]

    # -- 查询能力 --
    def objects(self, type_name: str) -> List[Object]:
        return self.object_type(type_name).all()

    def get(self, type_name: str, pk: Any) -> Optional[Object]:
        return self.object_type(type_name).get(pk)

    def search(self, type_name: str, **equals: Any) -> List[Object]:
        return self.object_type(type_name).search(**equals)

    def linked(self, obj: Object, link_name: str) -> List[Object]:
        """
        顺着链接走,拿到关联对象。既支持正向(Order->customer,返回 1 个),
        也支持反向(Customer->orders,返回多个)。这就是"沿关系遍历",
        agent 无需写任何 JOIN。
        """
        # 正向:link.name,源类型 == obj 的类型
        for lt in self._link_types.values():
            if lt.name == link_name and lt.source == obj.object_type.name:
                fk = obj[lt.foreign_key]
                target = self.get(lt.target, fk)
                return [target] if target else []
        # 反向:link.reverse_name,目标类型 == obj 的类型
        for lt in self._link_types.values():
            if lt.reverse_name == link_name and lt.target == obj.object_type.name:
                return self.search(lt.source, **{lt.foreign_key: obj.pk})
        raise KeyError(f"对象 {obj!r} 上没有名为 {link_name!r} 的链接")

    # -- 执行动作(唯一的写入口) --
    def execute_action(self, action_name: str, params: Dict[str, Any],
                       user: str = "anonymous") -> ActionResult:
        action = self.action(action_name)
        resolved = self._resolve_params(action, params)
        ctx = ActionContext(params=resolved, user=user, ontology=self)

        # 1) 先跑提交校验:任何一条不过,动作被拒绝,绝不写数据
        failed = [c.message for c in action.submission_criteria if not c.predicate(ctx)]
        if failed:
            raise ActionDenied(failed)

        # 2) 校验全过 -> 执行规则,产生受控的、可审计的编辑
        editor = OntologyEditor(self, user)
        action.apply(editor, ctx)
        return ActionResult(action=action_name, edits=editor.edits, by=user)

    def _resolve_params(self, action: ActionType, params: Dict[str, Any]) -> Dict[str, Any]:
        """校验必填 + 类型,并把 object<T> 参数解析成真正的 Object 实例。"""
        resolved: Dict[str, Any] = {}
        for p in action.parameters:
            if p.name not in params or params[p.name] in (None, ""):
                if p.required:
                    raise ActionDenied([f"缺少必填参数: {p.name}"])
                resolved[p.name] = None
                continue
            value = params[p.name]
            if p.type.startswith("object<") and p.type.endswith(">"):
                target_type = p.type[len("object<"):-1]
                obj = self.get(target_type, value)
                if obj is None:
                    raise ActionDenied([f"参数 {p.name} 指向的 {target_type} 不存在: {value!r}"])
                resolved[p.name] = obj
            else:
                resolved[p.name] = _coerce(value, p.type)
        return resolved
