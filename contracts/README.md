# contracts · 契约目录

**这里是唯一真源。** 前端的 TypeScript 类型、后端的 Pydantic model 都由本目录生成；
任何一方发现接口对不上，改的是这里，不是自己那边的代码。

## 文件

| 文件 | 内容 | 谁产出 | 谁消费 |
|---|---|---|---|
| `schema.sql` | 20 张表的全量建表语句 | S0 | 全体后端 |
| `seed.sql` | 初始化数据：默认组、管理员、RBAC 权限点 | S0 | 全体后端 |
| `openapi.yaml` | 65 个接口的完整定义（含请求/响应 schema） | S0 | 全体 |
| `sse-protocol.md` | 9 类流式事件的字段定义与完整示例流 | S4 | S11 / S18 / S19 |
| `errors.md` | 统一错误码与中文提示文案 | S0 | 全体 |

---

## 按垂直切片索引

每个人只需要看自己这一行。`openapi.yaml` 里的 `tag` 就是切片，用 Swagger UI 按 tag 折叠即可。

| 切片 | 名称 | 表 | OpenAPI tag | 接口数 | 其他契约 |
|---|---|---|---|---|---|
| **S2** | 身份与权限 | `sys_user` `sys_group` `sys_user_group` `sys_role_permission` | — | — | `errors.md` 通用段 |
| **S3** | 登录与用户管理 | 同上 | `auth` `users` | 12 | `errors.md` 认证与用户段 |
| **S4** | Agent 运行时 | `run` `run_event` | `runs` | 4 | **`sse-protocol.md` 全文** |
| **S5** | 运行时工具层 | `resource_mcp_tool` | — | — | `McpTool.input_schema` |
| **S6** | 智能体 CRUD 与版本 | `agent` `agent_meta` `agent_publish_log` | `agents` | 8 | |
| **S7** | 智能体列表页 | 同上 | `agents` · `GET /agents` | 1 | `AgentListItem` |
| **S8–S9** | 配置页·基础信息 / 模型与 Prompt | `agent` | `agents` · `GET/PUT /agents/{id}` | 2 | `AgentConfig` `Visibility` |
| **S10** | 配置页·资源挂载 | `agent_resource` | `agents` · `PUT /agents/{id}/resources` | 1 | `MountedResource` |
| **S11** | 即时测试与发布检查 | `run` | `agents` · `/precheck` `/publish` + `runs` | 3 | `PrecheckItem`、`sse-protocol.md` |
| **S12** | 资源·模型 | `resource_model` | `resources-models` | 6 | |
| **S13** | 资源·MCP/API | `resource_mcp` `resource_mcp_tool` | `resources-mcp` | 8 | `Diagnosis` |
| **S14** | 资源·Skill | `resource_skill` | `resources-skills` | 7 | `SkillInputVar` |
| **S15** | 资源·知识库 | `resource_knowledge` | `resources-knowledge` | 4 | `KnowledgeProvider` 接口 |
| **S16** | 会话与消息持久化 | `conversation` `message` `citation` | `conversations` | 6 | |
| **S17** | 问答页·会话管理 | 同上 | `conversations` | 6 | |
| **S18** | 问答页·流式渲染 | — | `runs` · `/stream` | 1 | **`sse-protocol.md` 全文** |
| **S19** | 问答页·引用与工具调用 | `citation` | — | — | `Citation`、`sse-protocol.md` 第 3–5 节 |
| **S20** | 回答反馈 | `feedback` | `feedback` | 1 | |
| **S21** | 附件上传 | `attachment` | `attachments` | 2 | |
| **S22** | 使用记录与对话详情 | `run` `run_event` | `records` + `runs` · `GET /runs/{id}` | 2 | `RunDetail` |
| **S23** | 发布记录与配置变更 | `agent_publish_log` `agent` | `records` `agents` · `/versions` | 4 | `ConfigChange` |

---

## 六条不许违反的约定

1. **契约变更走 PR。** 改 `contracts/` 下任何文件都必须开 PR，并在群里通知全员。
   CI 会校验 `openapi.yaml` 合法性与前后端类型是否同步。
2. **不许手改生成物。** `frontend/src/api/`、后端的 schema 生成目录都是自动生成的，
   手改会在下次生成时丢失。
3. **`agent` 表的 `version_no=0` 是草稿行。** 任何写操作只能改 v0；发布是拷贝 v0
   成新版本。**永远不要 UPDATE `version_no >= 1` 的行。**
4. **软删不硬删。** 所有删除写 `deleted_at`，查询默认过滤。
5. **密钥永不入库明文、永不回前端。** `*_enc` 字段用 `CREDENTIAL_ENC_KEY` 加密，
   接口一律返回 `*_masked` 形式。
6. **错误 message 必须是可以直接展示给用户的中文。** 不道歉、不含糊、说清楚怎么办；
   堆栈、SQL、内网地址一律只进日志。

---

## 三个已定的设计取舍

写在这里，避免后面有人以为是漏了。

1. **资源挂载用全量替换**（`PUT /agents/{id}/resources`）而不是逐条增删。
   前端一次提交全部挂载关系，后端整体覆盖。少四个接口，且天然避免并发下的状态不一致。
2. **配置变更记录没有独立的表。** 由相邻两个版本快照的 `config` JSONB 做 diff 实时生成，
   操作人与时间取自该版本行的 `created_by` / `created_at`。因此不需要在每个写接口里埋点。
3. **即时测试复用运行接口。** `mode=TEST` 与 `mode=CHAT` 走同一条链路，
   区别只在 TEST 不写 `conversation` / `message`，只写 `run` 与 `run_event`。
   这让「即时测试」这个切片的边际成本接近零。

---

## 本地怎么用

```bash
# 建库（容器首次启动会自动执行 schema.sql 与 seed.sql）
cd deploy && docker compose up -d

# 校验 openapi.yaml
npx @redocly/cli lint contracts/openapi.yaml

# 生成前端 TS 类型与客户端
npx openapi-typescript contracts/openapi.yaml -o frontend/src/api/schema.d.ts

# 起 mock server（骨架未就绪时前端用它开发）
npx @stoplight/prism-cli mock contracts/openapi.yaml --port 4010
```

> SSE 无法由 mock server 模拟。流式部分用后端的假 SSE 服务
> （按 `sse-protocol.md` 的示例流定时吐帧），见启动手册 Day 3。

---

## 默认登录

`seed.sql` 建了一个管理员：**admin / Admin@123**。
这是开发环境的默认凭据，**部署到任何共享环境前必须改掉**。
