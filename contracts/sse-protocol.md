# SSE 事件协议

> 契约文件，唯一真源。切片 **S4**（运行时）产出，**S11 / S18 / S19**（即时测试、流式渲染、引用面板）消费。
> 修改必须走 PR 并通知全员。

## 传输约定

| 项 | 约定 |
|---|---|
| 端点 | `GET /api/runs/{run_id}/stream` |
| Content-Type | `text/event-stream; charset=utf-8` |
| 编码 | 每帧 `event: <类型>\ndata: <JSON>\n\n` |
| 心跳 | 每 15 秒发一行注释帧 `: keep-alive`，防止代理断连 |
| Nginx | 该路径必须 `proxy_buffering off; proxy_read_timeout 600s;` |
| 认证 | `Authorization: Bearer <JWT>`；EventSource 不支持自定义头，前端用 fetch + ReadableStream |
| 结束 | 服务端在 `message.done` / `run.error` / `run.cancelled` 之后关闭连接 |
| 落库 | 每发出一帧，同步写一行 `run_event`，`seq` 从 0 自增 |

## 事件总览

一次成功的运行，事件顺序固定为：

```
run.started
  ├─ thinking.step        （0..N，随推理推进）
  ├─ tool.call            （0..N）
  │   └─ tool.result      （与 tool.call 一一对应，靠 call_id 关联）
  ├─ citation             （0..N，可在工具结果或知识检索后出现）
  └─ message.delta        （1..N，逐块文本）
message.done
```

失败或取消时，以 `run.error` 或 `run.cancelled` 收尾，替代 `message.done`。

---

## 1. `run.started`

运行开始。前端收到后进入"回答中"状态。

```json
{
  "run_id": "10231",
  "agent_id": "48",
  "version_no": 3,
  "mode": "CHAT",
  "started_at": "2026-09-01T10:12:03.412+08:00"
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `run_id` | string | 是 | 与 `POST /api/runs` 返回值一致 |
| `agent_id` | string | 是 | |
| `version_no` | integer | 是 | 实际执行的版本；`mode=TEST` 时为 0（草稿） |
| `mode` | string | 是 | `CHAT` \| `TEST` |
| `started_at` | string | 是 | RFC3339 带时区 |

## 2. `thinking.step`

思考过程的一步。前端追加到可折叠的"思考过程"区。

```json
{
  "step_id": "s1",
  "phase": "RETRIEVE",
  "text": "检索设备知识库，筛选 3 号泵相关的停机规程"
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `step_id` | string | 是 | 本次运行内唯一 |
| `phase` | string | 是 | `IDENTIFY` 识别 \| `RETRIEVE` 检索 \| `INVOKE` 调用 |
| `text` | string | 是 | 一句话，≤120 字，面向业务用户，不含技术黑话 |

## 3. `tool.call`

开始调用一个工具。前端渲染"正在调用 xx"的卡片。

```json
{
  "call_id": "c1",
  "tool_name": "alarm_log_query",
  "tool_label": "报警日志查询",
  "tool_type": "MCP",
  "args_summary": "设备=3号泵，时间范围=近7天"
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `call_id` | string | 是 | 与 `tool.result` 配对 |
| `tool_name` | string | 是 | 传给模型的工具名 |
| `tool_label` | string | 是 | 展示用中文名 |
| `tool_type` | string | 是 | `MCP` \| `KNOWLEDGE` \| `SKILL` |
| `args_summary` | string | 是 | 人话摘要，**不得包含密钥或完整原始参数** |

## 4. `tool.result`

工具调用结束。

```json
{
  "call_id": "c1",
  "status": "OK",
  "duration_ms": 823,
  "summary": "命中 12 条报警记录",
  "error_message": null
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `call_id` | string | 是 | |
| `status` | string | 是 | `OK` \| `FAILED` \| `TIMEOUT` |
| `duration_ms` | integer | 是 | |
| `summary` | string | 是 | 结果摘要，非全量数据 |
| `error_message` | string \| null | 否 | `status != OK` 时给出人话原因 |

> 工具失败**不终止运行**：模型会拿到失败信息继续推理。只有整个运行失败才发 `run.error`。

## 5. `citation`

一条引用来源。前端追加到右侧引用面板，并让顶部计数 +1。

```json
{
  "citation_id": "r1",
  "source_type": "KNOWLEDGE",
  "source_name": "设备管理知识库",
  "title": "离心泵常见停机原因与处置",
  "snippet": "当轴承温度超过 75℃ 时联锁停机……",
  "ref": "doc_88231#p12"
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `citation_id` | string | 是 | |
| `source_type` | string | 是 | `KNOWLEDGE` 知识库 \| `TOOL` 工具结果 |
| `source_name` | string | 是 | 知识库名或工具名 |
| `title` | string | 是 | |
| `snippet` | string | 是 | 摘要，≤200 字 |
| `ref` | string \| null | 否 | 原文定位标识 |

## 6. `message.delta`

回答正文的一个增量块。前端**追加**，不是替换。

```json
{ "text": "根据设备手册，" }
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `text` | string | 是 | 增量文本，可能是单字也可能是一段 |

## 7. `message.done`

回答结束，运行成功。

```json
{
  "message_id": "88121",
  "finish_reason": "STOP",
  "duration_ms": 12403,
  "citation_count": 3,
  "tool_call_count": 2
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `message_id` | string \| null | 是 | `mode=TEST` 时为 null（不落消息） |
| `finish_reason` | string | 是 | `STOP` \| `LENGTH` \| `TOOL_LIMIT` |
| `duration_ms` | integer | 是 | |
| `citation_count` | integer | 是 | 前端应与实际收到的 citation 条数一致 |
| `tool_call_count` | integer | 是 | |

## 8. `run.error`

运行失败。

```json
{
  "code": "MODEL_UNAVAILABLE",
  "message": "模型服务暂时不可用，请稍后重试"
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `code` | string | 是 | 见 `errors.md` |
| `message` | string | 是 | 可直接展示给用户的中文提示 |

## 9. `run.cancelled`

用户取消。

```json
{ "reason": "USER_CANCELLED", "partial_text_length": 142 }
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `reason` | string | 是 | `USER_CANCELLED` \| `TIMEOUT` |
| `partial_text_length` | integer | 是 | 已输出字符数，供记录页展示"未完成输出" |

---

## 完整示例流

mock server 和前端联调都以这段为准。

```
event: run.started
data: {"run_id":"10231","agent_id":"48","version_no":3,"mode":"CHAT","started_at":"2026-09-01T10:12:03.412+08:00"}

event: thinking.step
data: {"step_id":"s1","phase":"IDENTIFY","text":"识别为设备故障归因类问题，对象=3号泵，时间=近7天"}

event: thinking.step
data: {"step_id":"s2","phase":"RETRIEVE","text":"检索设备管理知识库，筛选停机规程"}

event: citation
data: {"citation_id":"r1","source_type":"KNOWLEDGE","source_name":"设备管理知识库","title":"离心泵常见停机原因与处置","snippet":"当轴承温度超过 75℃ 时联锁停机……","ref":"doc_88231#p12"}

event: thinking.step
data: {"step_id":"s3","phase":"INVOKE","text":"调用报警日志查询，获取近 7 天报警明细"}

event: tool.call
data: {"call_id":"c1","tool_name":"alarm_log_query","tool_label":"报警日志查询","tool_type":"MCP","args_summary":"设备=3号泵，时间范围=近7天"}

event: tool.result
data: {"call_id":"c1","status":"OK","duration_ms":823,"summary":"命中 12 条报警记录","error_message":null}

event: citation
data: {"citation_id":"r2","source_type":"TOOL","source_name":"报警日志查询","title":"3号泵 近7天报警明细","snippet":"轴承温度高报 5 次，振动超限 4 次……","ref":"call:c1"}

event: message.delta
data: {"text":"根据设备手册与近 7 天报警记录，"}

event: message.delta
data: {"text":"3 号泵停机的主要原因是轴承温度高报联锁。"}

event: message.done
data: {"message_id":"88121","finish_reason":"STOP","duration_ms":12403,"citation_count":2,"tool_call_count":1}
```
