# 客服 Agent 接入客户360

基于 [MaxKB](https://github.com/1Panel-dev/MaxKB) 搭建面向**外部客户**的智能客服 Agent，
并把它的客服能力安全地提供给公司内部的**客户360 系统**。

## 这个仓库提供什么

MaxKB 本身负责知识库、工作流和模型编排（直接用官方 Docker 镜像，不改源码）。
本仓库补齐的是它做不了、但你们场景必需的那部分——**让 AI 能查客户真实数据，同时保证查不到别人的数据**。

```
services/chat-gateway/   对话网关：认身份、建会话、流式转发、限流脱敏审计、转人工
services/tool-api/       业务工具 API：给 MaxKB 工作流查订单/工单，强制按客户隔离
web/demo/index.html      前端接入示例（含 SSE 打字机效果）
scripts/smoke_test.sh    安全冒烟测试：验证「查得到自己、查不到别人」
docs/                    架构方案、实施步骤、MaxKB 后台配置指南
```

## 快速开始

```bash
cp services/tool-api/.env.example     services/tool-api/.env
cp services/chat-gateway/.env.example services/chat-gateway/.env
# 编辑两个 .env：填 MAXKB_API_KEY，并把 TOOL_API_SERVICE_KEY 设成同一个随机长字符串

docker compose up -d --build

export TOOL_API_SERVICE_KEY=你设置的值
./scripts/smoke_test.sh          # 6 项安全检查，全过才算通
```

然后用浏览器打开 `web/demo/index.html` 试聊。默认 `TOOL_DATA_SOURCE=mock`，
用内置示例数据先把链路跑通，再切到真实数据库或接口。

## 核心设计：一条铁律

> **customer_id 只能由网关从登录态推导，绝不经过前端，也绝不经过大模型。**

MaxKB 的工作流查数据时只带自己的 `chat_id`，由 `tool-api` 反查客户身份并强制过滤。
大模型全程不知道任何客户 ID，也就无法编造一个去查别人的数据。
详见 [docs/01-架构方案.md](docs/01-架构方案.md)。

## 文档

| 文档 | 内容 |
|---|---|
| [01-架构方案](docs/01-架构方案.md) | 整体架构、安全设计、许可证提醒 |
| [02-实施步骤](docs/02-实施步骤.md) | 从装 MaxKB 到灰度上线的 10 步，含上线检查清单 |
| [03-MaxKB配置指南](docs/03-MaxKB配置指南.md) | 后台怎么点：API Key、嵌入第三方、工作流 HTTP 节点 |

## 上线前必看

- `AUTH_MODE` 必须从 `debug` 改成 `jwt` 或 `introspect`
- `tool-api` 只能监听内网
- 内存版的会话表和限流器在多副本部署时要换成 Redis
- MaxKB 是 GPLv3，本方案只调 API 不改源码属于低风险用法
