"""
把迷你 Ontology 暴露成 MCP 工具 (agent-ready)
============================================

这一层对应 Palantir 的 **Ontology MCP (OMCP)**:它把 Ontology 的读能力和
动作,包装成标准 MCP 工具,任何 MCP 客户端(Claude、nexent、Copilot Studio…)
连上后就能直接调用 —— 无需了解底层数据。

运行(stdio 传输,给 MCP 客户端连接):
    pip install "mcp[cli]"
    python mcp_server.py

在 nexent 里:把它作为一个第三方 MCP server 挂上去即可(见 README)。

设计对照 OMCP:
    OMCP: 每个 object type -> 一个查询工具        本文件: query_objects / get_object
    OMCP: 沿 link 遍历                            本文件: traverse_link
    OMCP: 每个 action type -> 一个可执行工具       本文件: execute_action(+ list_actions 自描述)
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from ontology import ActionDenied, build_ontology

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # 友好提示,而不是直接崩
    raise SystemExit(
        "需要先安装 MCP SDK:  pip install \"mcp[cli]\"\n"
        "(只想看 Ontology 概念、不接 agent 的话,直接跑 python demo.py 即可。)"
    )

# 整个 server 进程共享同一个 Ontology 实例(动作产生的编辑会在进程内持久)
ONTO = build_ontology()
mcp = FastMCP("order-ontology")


# --------------------------- 读:对象与链接 ---------------------------

@mcp.tool()
def list_object_types() -> str:
    """列出 Ontology 里所有对象类型及其主键/标题/属性(agent 用来了解有什么数据)。"""
    out = []
    for name in ONTO.object_type_names():
        ot = ONTO.object_type(name)
        out.append({
            "name": ot.name,
            "primary_key": ot.primary_key,
            "title_key": ot.title_key,
            "properties": {p.name: p.type for p in ot.properties.values()},
            "count": len(ot.rows),
        })
    return json.dumps(out, ensure_ascii=False, indent=2)


@mcp.tool()
def query_objects(object_type: str, filters: Optional[Dict[str, Any]] = None) -> str:
    """
    按属性等值过滤,查询某类对象。
    例:object_type="Order", filters={"status": "unshipped"}
    """
    objs = ONTO.search(object_type, **(filters or {}))
    return json.dumps([o.to_dict() for o in objs], ensure_ascii=False, default=str, indent=2)


@mcp.tool()
def get_object(object_type: str, primary_key: str) -> str:
    """按主键获取单个对象。例:object_type="Order", primary_key="O1001"。"""
    obj = ONTO.get(object_type, primary_key)
    if obj is None:
        return json.dumps({"error": f"未找到 {object_type} {primary_key}"}, ensure_ascii=False)
    return json.dumps(obj.to_dict(), ensure_ascii=False, default=str, indent=2)


@mcp.tool()
def traverse_link(object_type: str, primary_key: str, link_name: str) -> str:
    """
    从一个对象沿链接遍历到关联对象(无需写 JOIN)。
    例:从客户拿其所有订单 -> object_type="Customer", primary_key="C001", link_name="orders"
    """
    obj = ONTO.get(object_type, primary_key)
    if obj is None:
        return json.dumps({"error": f"未找到 {object_type} {primary_key}"}, ensure_ascii=False)
    linked = ONTO.linked(obj, link_name)
    return json.dumps([o.to_dict() for o in linked], ensure_ascii=False, default=str, indent=2)


# --------------------------- 写:动作 ---------------------------

@mcp.tool()
def list_actions() -> str:
    """列出所有可执行动作及其参数说明(agent 用来知道能"做"什么)。"""
    out = []
    for name in ONTO.action_names():
        a = ONTO.action(name)
        out.append({
            "name": a.name,
            "description": a.description,
            "parameters": [
                {"name": p.name, "type": p.type, "required": p.required, "description": p.description}
                for p in a.parameters
            ],
        })
    return json.dumps(out, ensure_ascii=False, indent=2)


@mcp.tool()
def execute_action(action_name: str, parameters: Dict[str, Any], user: str = "agent:mcp") -> str:
    """
    执行一个动作(唯一的写入口)。会先跑提交校验,不通过则被拒绝、数据不变。
    例:action_name="cancel_order", parameters={"order": "O1001", "reason": "用户取消"}
    """
    try:
        result = ONTO.execute_action(action_name, parameters, user=user)
    except ActionDenied as err:
        return json.dumps({"status": "denied", "reasons": err.reasons}, ensure_ascii=False, indent=2)
    except KeyError as err:
        return json.dumps({"status": "error", "message": str(err)}, ensure_ascii=False)
    return json.dumps(
        {"status": "ok", "action": result.action, "by": result.by, "edits": result.edits},
        ensure_ascii=False, default=str, indent=2,
    )


if __name__ == "__main__":
    mcp.run()
