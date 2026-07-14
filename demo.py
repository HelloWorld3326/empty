"""
迷你 Ontology 演示脚本
=====================
直接运行:  python demo.py

它会走一遍"业务人员/agent 用自然语言操作业务"背后的真实步骤:
查对象 -> 沿链接遍历 -> 执行动作(成功) -> 执行动作(被校验拦截) -> 新建对象。
"""

from ontology import ActionDenied, build_ontology


def hr(title: str) -> None:
    print("\n" + "=" * 64)
    print(title)
    print("=" * 64)


def main() -> None:
    onto = build_ontology()

    # ------------------------------------------------------------------
    hr("① 读对象:列出所有订单(每个对象 = 表里的一行)")
    for o in onto.objects("Order"):
        print(f"  {o.pk}  {o['item_name']:<12} ¥{o['amount']:<8} {o['status']}")

    # ------------------------------------------------------------------
    hr("② 沿链接遍历:找到客户'张三',再列出他的所有订单")
    zhangsan = onto.search("Customer", name="张三")[0]
    print(f"  客户: {zhangsan!r}  等级={zhangsan['tier']}")
    orders = onto.linked(zhangsan, "orders")           # 反向链接 customer.orders
    for o in orders:
        print(f"    - {o.pk} {o['item_name']} ({o['status']})")

    # ------------------------------------------------------------------
    hr("③ 定位目标:张三 '未发货' 的订单(这就是 agent 要取消的那笔)")
    target = [o for o in orders if o["status"] == "unshipped"]
    for o in target:
        # 沿正向链接 order.customer 反查客户,验证关系可双向走
        cust = onto.linked(o, "customer")[0]
        print(f"  找到: {o.pk} {o['item_name']}  属于客户 {cust['name']}")

    to_cancel = target[0]

    # ------------------------------------------------------------------
    hr("④ 执行动作 cancel_order:提交校验通过 -> 状态被改,且留下审计记录")
    result = onto.execute_action(
        "cancel_order",
        {"order": to_cancel.pk, "reason": "用户主动取消"},
        user="agent:nexent",
    )
    print(f"  动作: {result.action}  执行者: {result.by}")
    for e in result.edits:
        print(f"  审计: {e}")
    print(f"  现在 {to_cancel.pk} 的状态 = {onto.get('Order', to_cancel.pk)['status']}")

    # ------------------------------------------------------------------
    hr("⑤ 提交校验的威力:试图取消一笔'已发货'订单 -> 被拒绝,数据不变")
    shipped = onto.search("Order", status="shipped")[0]
    print(f"  尝试取消已发货订单 {shipped.pk} ({shipped['item_name']}) ...")
    try:
        onto.execute_action("cancel_order", {"order": shipped.pk}, user="agent:nexent")
    except ActionDenied as err:
        print(f"  ❌ 被拒绝: {err.reasons}")
    print(f"  {shipped.pk} 状态仍为 = {onto.get('Order', shipped.pk)['status']}(未被改动)")

    # ------------------------------------------------------------------
    hr("⑥ 执行动作 place_order:为李四新建一笔订单(Create object 规则)")
    lisi = onto.search("Customer", name="李四")[0]
    res2 = onto.execute_action(
        "place_order",
        {"customer": lisi.pk, "item_name": "无线鼠标", "amount": 129.0},
        user="agent:nexent",
    )
    for e in res2.edits:
        print(f"  审计: {e}")
    print(f"  李四现在的订单数: {len(onto.linked(lisi, 'orders'))}")

    hr("完成 ✅  你刚刚见证了:对象 / 链接 / 动作 / 提交校验 全流程")


if __name__ == "__main__":
    main()
