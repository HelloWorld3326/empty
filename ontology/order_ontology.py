"""
订单管理 Ontology 的定义 (the order-management ontology)
======================================================

这个文件就是"你在 Ontology Manager 里点出来的东西"的代码版本:
定义两个对象类型、一条链接、两个动作。看这个文件,就能对照理解
你在 Palantir UI 里每一步在做什么。
"""

from __future__ import annotations

import os

from .core import (
    ActionContext,
    ActionType,
    Criterion,
    LinkType,
    ObjectType,
    OntologyEditor,
    Ontology,
    Parameter,
    Property,
)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")

# 可被取消的订单状态(业务规则:已发货/已送达/已取消的单子不能再取消)
CANCELLABLE_STATUSES = {"unshipped", "pending"}


def build_ontology() -> Ontology:
    onto = Ontology()

    # =====================================================================
    # ① 对象类型 Customer  <-- 对应 Ontology Manager: Create object type
    #    backing dataset = data/customers.csv
    #    primary key = customer_id, title key = name
    # =====================================================================
    customer = ObjectType.from_csv(
        name="Customer",
        csv_path=os.path.join(DATA_DIR, "customers.csv"),
        primary_key="customer_id",
        title_key="name",
        properties=[
            Property("customer_id", "string", "客户ID"),
            Property("name", "string", "姓名"),
            Property("tier", "string", "会员等级"),
            Property("email", "string", "邮箱"),
        ],
    )
    onto.add_object_type(customer)

    # =====================================================================
    # ② 对象类型 Order
    #    backing dataset = data/orders.csv
    #    primary key = order_id, title key = item_name
    # =====================================================================
    order = ObjectType.from_csv(
        name="Order",
        csv_path=os.path.join(DATA_DIR, "orders.csv"),
        primary_key="order_id",
        title_key="item_name",
        properties=[
            Property("order_id", "string", "订单ID"),
            Property("order_number", "string", "订单号"),
            Property("customer_id", "string", "客户ID(外键)"),
            Property("item_name", "string", "商品名称"),
            Property("amount", "double", "金额"),
            Property("status", "string", "状态"),
            Property("created_at", "date", "下单日期"),
        ],
    )
    onto.add_object_type(order)

    # =====================================================================
    # ③ 链接类型 Order --customer--> Customer  (多对一,外键 customer_id)
    #    对应 Ontology Manager: Create link type
    #    正向: order.customer  反向: customer.orders
    # =====================================================================
    onto.add_link_type(LinkType(
        name="customer",         # 正向:一个订单 -> 它的客户
        source="Order",
        target="Customer",
        foreign_key="customer_id",
        reverse_name="orders",   # 反向:一个客户 -> 他的所有订单
    ))

    # =====================================================================
    # ④ 动作类型 cancel_order  <-- 对应 Ontology Manager: Create action type
    #    参数 + 提交校验 + 规则,三件套齐全
    # =====================================================================

    def cancel_order_apply(editor: OntologyEditor, ctx: ActionContext) -> None:
        # 规则(Ontology edit rule): Modify object,把订单状态改成 cancelled
        order_obj = ctx.param("order")
        editor.modify_object(
            order_obj,
            status="cancelled",
            cancel_reason=ctx.param("reason"),
        )

    onto.add_action(ActionType(
        name="cancel_order",
        description="取消一笔订单。只有处于可取消状态(未发货/待处理)的订单才允许取消。",
        parameters=[
            Parameter("order", "object<Order>", "要取消的订单(传订单ID)"),
            Parameter("reason", "string", "取消原因", required=False),
        ],
        submission_criteria=[
            # 校验:订单当前状态必须是可取消的。守住业务规则的就是这一条。
            Criterion(
                message="该订单当前状态不允许取消(只有未发货/待处理的订单可取消)。",
                predicate=lambda ctx: ctx.param("order")["status"] in CANCELLABLE_STATUSES,
            ),
        ],
        apply=cancel_order_apply,
    ))

    # =====================================================================
    # ⑤ 动作类型 place_order  (演示 Create object 规则)
    # =====================================================================

    def place_order_apply(editor: OntologyEditor, ctx: ActionContext) -> None:
        customer_obj = ctx.param("customer")
        new_id = _next_order_id(ctx.ontology.object_type("Order"))
        editor.create_object(
            "Order",
            order_id=new_id,
            order_number="ORD-" + new_id[1:],
            customer_id=customer_obj.pk,
            item_name=ctx.param("item_name"),
            amount=ctx.param("amount"),
            status="pending",
            created_at=None,
        )

    onto.add_action(ActionType(
        name="place_order",
        description="为某个客户下一笔新订单。金额必须为正数。",
        parameters=[
            Parameter("customer", "object<Customer>", "下单客户(传客户ID)"),
            Parameter("item_name", "string", "商品名称"),
            Parameter("amount", "double", "金额"),
        ],
        submission_criteria=[
            Criterion(
                message="金额必须大于 0。",
                predicate=lambda ctx: (ctx.param("amount") or 0) > 0,
            ),
        ],
        apply=place_order_apply,
    ))

    return onto


def _next_order_id(order_type: ObjectType) -> str:
    """生成下一个订单ID,如 O1006。演示用的简单自增。"""
    nums = [int(r["order_id"][1:]) for r in order_type.rows if r.get("order_id")]
    return "O" + str((max(nums) if nums else 1000) + 1)
