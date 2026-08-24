# DataAgent：对标 DeerFlow 2.0 的数据智能体

> 完整版本（含分层图、排期泳道、风险表）见同目录 `dataagent-mvp-blueprint.html`。
> 基准：`bytedance/deer-flow` @ main，末次提交 2026-08-24，规模数据为本地 clone 实测。

## 关键事实

最新版 deer-flow 是 **DeerFlow 2.0**，官方 `docs/ARCHITECTURE.md` 明确说明它是彻底重写，
与 v1 的 Deep Research 框架不共享任何代码。它是一个 **Super-Agent Harness**（类 Claude Code 的
通用 Agent 平台），而非「输入课题产出研究报告」的流水线。

实测规模：

| 指标 | 数值 |
|---|---|
| 后端 Python 文件 | 1,207 个 |
| 后端总行数 | 402,152 行（非测试 150,958 行） |
| 前端 TS/TSX | 419 个 / 60,481 行 |
| Gateway REST Router | 24 个 |
| IM 渠道适配 | 12 个 |

## 决策记录

| 决策项 | 结论 |
|---|---|
| 对标对象 | DeerFlow 2.0 Super-Agent Harness |
| 实现方式 | 自建仓库 + 架构同构 + 直接依赖 LangGraph（不 fork） |
| 业务主线 | 取数问答 Text-to-SQL + 元数据/资产盘点问答 |
| 数据源 | MVP 打通 PostgreSQL + MySQL；StarRocks 进完整版 |
| 数据安全 | 只读账号 · 表白名单 50–200 张 · SELECT-only · 强制 LIMIT/超时/行数上限 |
| 元数据 | 无现成平台，注释覆盖 ~80% → 自建表卡片，LLM 半自动生成 + 人工校对 |
| 知识库 | 调用公司现成向量库 API，不自建 RAG |
| 编排 | 单 Agent + Tools 循环（ReAct），按多 agent 骨架写 |
| 代码执行 | MVP 零沙箱，图表由前端 ECharts 渲染 |
| 模型 | 阿里云百炼，OpenAI 兼容 ModelProvider，默认 qwen-max |
| 合规 | 表结构与查询结果均可进 prompt |
| 前端 | Vue 自研，API 契约对齐 DeerFlow thread/run/message/tool_call/artifact 语义 |
| 认证授权 | 账号密码 + session；统一数据权限，role/allowed_roles 占位；全量 SQL 审计 |
| 存储 | Postgres 单库 + S3；不用 Redis、不用 MQ |
| 部署 | K8s replicas=1 + Recreate；重启后 running → interrupted 可重试 |
| 扩展点 | 只做工具注册表抽象；Skills/MCP/渠道进完整版 |
| 记忆 | 仅会话内上下文；落 qa_samples 只写不读 |
| 验收基线 | 30–50 题黄金测试集，第 1 周建立，每日自动回归 |

## 待确认假设

- **A1** 没有干系人明确要求「跑 Python 分析」→ 零沙箱写死，预留 3 人天 pandas 后处理预案。
- **A2** 能拿到历史 SQL 日志 + 业务方派 1 人配合 2–3 天。
- **A3** 架构负责人每周 3 天编码（合计 12 个编码日）。
- **A4** 团队按 4 人排基线，第 5 人当加速器。

## 里程碑（D1 = 2026-08-25）

| 里程碑 | 日期 | 内容 |
|---|---|---|
| M1 | D4 · 08-28 | 契约冻结：DDL + REST + SSE + mock 流，前端解除阻塞 |
| M2 | D9 · 09-04 | Hello Agent 端到端贯通 |
| M3 | D12 · 09-09 | 主线打通：NL → SQL → 结果 → 图表 → 解读 |
| M4 | D15 · 09-14 | 准确率首次评估，决定是否收窄白名单 |
| M5 | D16 · 09-15 | 功能冻结 |
| M6 | D20 · 09-21 | 演示与验收 |

## 关键路径

```
D3 SSE 协议冻结 → D7 编排引擎 → D9 端到端贯通 → D12 主线可演示 → D16 冻结 → D20 演示
```

五个环节中四个在架构负责人手上，是本排期最大的单点风险。

## Top 风险

1. **R1 准确率不达标** — D15 是决策日，应对手段为收窄白名单、补表卡片口径、few-shot 救场。
2. **R2 验收期待错配** — D4 必须让验收方在范围文档（含「零沙箱」）上签字。
3. **R3 拿不到黄金测试集** — 降级为每周试用会，但必须上报「本项目将没有客观准确率指标」。
4. **R4 架构负责人单点** — 周投入低于 3 天时，切走 run 生命周期落库，M2 顺延至 D10。

## 演进路线

| 增量 | 内容 | 预估 |
|---|---|---|
| I1 | qa_samples 接成 few-shot 检索 + 反馈闭环 | 2–3 周 |
| I2 | StarRocks 方言 + 多数据源路由 | 1–2 周 |
| I3 | RBAC + 行级/表级数据权限 | 3–4 周 |
| I4 | 沙箱 + Python 分析 + 报告生成 | 4–6 周 |
| I5 | Skills / MCP / 子智能体 / IM 渠道 / 定时任务 | 按需 |
