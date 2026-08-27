# 统一错误码

> 契约文件。所有接口的失败响应统一为下面这个结构，HTTP 状态码 + 业务码 + 可直接展示的中文提示。

## 响应结构

```json
{
  "code": "AGENT_PUBLISHED_CANNOT_DELETE",
  "message": "已发布的智能体不能直接删除，请先下架",
  "detail": { "agent_id": "48", "status": "PUBLISHED" }
}
```

| 字段 | 说明 |
|---|---|
| `code` | 业务码，前端用它做分支判断 |
| `message` | **可直接展示给用户的中文**。说清楚出了什么问题、怎么解决，不道歉、不含糊 |
| `detail` | 可选，结构化上下文，用于前端做二次渲染（比如引用影响列表） |

## 通用（所有接口）

| HTTP | code | message | 切片 |
|---|---|---|---|
| 400 | `INVALID_PARAM` | 参数不合法：{字段名} | 全部 |
| 401 | `UNAUTHENTICATED` | 登录已过期，请重新登录 | S2 |
| 403 | `FORBIDDEN` | 你没有权限执行这个操作 | S2 |
| 404 | `NOT_FOUND` | 请求的内容不存在或已被删除 | 全部 |
| 409 | `CONFLICT` | 数据已被他人修改，请刷新后重试 | 全部 |
| 429 | `RATE_LIMITED` | 当前并发请求过多，请稍后重试 | S4 |
| 500 | `INTERNAL_ERROR` | 服务异常，请联系管理员 | 全部 |

## 认证与用户 · S3

| HTTP | code | message |
|---|---|---|
| 401 | `BAD_CREDENTIALS` | 用户名或密码错误 |
| 403 | `USER_DISABLED` | 该账号已被停用，请联系管理员 |
| 409 | `USERNAME_TAKEN` | 用户名已存在 |
| 400 | `WEAK_PASSWORD` | 密码需至少 8 位，包含字母和数字 |
| 409 | `GROUP_NOT_EMPTY` | 该组下还有成员，请先移出成员 |

## 智能体 · S6 / S7 / S11

| HTTP | code | message |
|---|---|---|
| 409 | `AGENT_PUBLISHED_CANNOT_DELETE` | 已发布的智能体不能直接删除，请先下架 |
| 409 | `AGENT_ALREADY_PUBLISHED` | 该智能体已是发布状态 |
| 409 | `AGENT_NEVER_PUBLISHED` | 该智能体尚未发布过，无法上架 |
| 422 | `PRECHECK_FAILED` | 发布前检查未通过，请查看检查项详情 |
| 400 | `PROMPT_REQUIRED` | 提示词不能为空 |
| 400 | `MODEL_NOT_AVAILABLE` | 所选模型已停用，请重新选择 |
| 400 | `RESOURCE_UNHEALTHY` | 挂载的资源存在异常，不建议发布 |

## 运行 · S4

| HTTP | code | message |
|---|---|---|
| 400 | `EMPTY_QUESTION` | 请输入问题内容 |
| 404 | `RUN_NOT_FOUND` | 该运行记录不存在 |
| 409 | `RUN_ALREADY_FINISHED` | 该运行已结束，无法取消 |
| 502 | `MODEL_UNAVAILABLE` | 模型服务暂时不可用，请稍后重试 |
| 502 | `MODEL_AUTH_FAILED` | 模型鉴权失败，请联系管理员检查密钥 |
| 504 | `MODEL_TIMEOUT` | 模型响应超时，请重试或缩短问题 |
| 400 | `CONTEXT_TOO_LONG` | 对话内容超出模型上下文，请新建对话或减少附件 |
| 500 | `TOOL_LIMIT_EXCEEDED` | 工具调用次数超出上限，请简化问题 |

> `MODEL_*` 与 `TOOL_*` 这几个码同时用于 HTTP 响应和 SSE 的 `run.error` 事件。

## 资源 · S12 / S13 / S14 / S15

| HTTP | code | message |
|---|---|---|
| 409 | `RESOURCE_IN_USE` | 该资源正被 {n} 个智能体引用，删除后这些智能体可能不可用 |
| 409 | `MODEL_DUPLICATE` | 该模型已接入，不能重复添加 |
| 502 | `MCP_UNREACHABLE` | 无法连接到服务地址，请检查网络与地址是否正确 |
| 502 | `MCP_AUTH_FAILED` | 鉴权失败，请检查鉴权方式与凭证 |
| 502 | `MCP_SCHEMA_INVALID` | 已连接但无法解析工具描述，该服务可能不是标准 MCP |
| 504 | `MCP_TIMEOUT` | 服务响应超时 |
| 400 | `SKILL_TEMPLATE_INVALID` | 模板中的变量 {name} 未在输入变量中声明 |
| 502 | `KNOWLEDGE_PROVIDER_UNAVAILABLE` | 内网知识库服务暂时不可用 |
| 409 | `KNOWLEDGE_ALREADY_REGISTERED` | 该知识库已登记 |

## 会话与附件 · S17 / S20 / S21

| HTTP | code | message |
|---|---|---|
| 400 | `TITLE_REQUIRED` | 会话名称不能为空 |
| 400 | `TITLE_TOO_LONG` | 会话名称最长 40 字 |
| 409 | `FEEDBACK_ALREADY_SUBMITTED` | 已对这条回答提交过反馈 |
| 413 | `FILE_TOO_LARGE` | 文件超过 {max} MB 上限 |
| 415 | `FILE_TYPE_NOT_ALLOWED` | 不支持该文件类型 |

## 写 message 的三条规则

1. **说清楚怎么办**，不只说出了什么错。`"模型鉴权失败，请联系管理员检查密钥"` 优于 `"鉴权失败"`。
2. **不道歉、不含糊**。不写"抱歉"、"可能"、"似乎"。
3. **不泄露内部细节**。堆栈、SQL、内网地址、密钥一律不进 `message`，只写日志。
