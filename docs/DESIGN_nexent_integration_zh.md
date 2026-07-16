# SkillOpt 技能优化 — nexent 原生集成设计（后端评审稿）

> 评审对象：架构、接口、数据模型、权限与模型集成方式。确认后开始写代码（只写后端）。
> 配套交互原型：`docs/prototype/skillopt-frontend-interactive-spec.html`（点击接口标注可看请求/响应示例）。

## 1. 需求映射

| 要求 | 设计落点 |
|---|---|
| 1. 权限按 nexent 项目 | 所有接口经 `utils/auth_utils.get_current_user_id(authorization)` 解析 (user_id, tenant_id)；任务/上传/产物按 **tenant_id 隔离**（跨租户一律 404）；菜单显隐走 `role_permission_t`（交付 SQL 迁移）；兼容 speed-mode |
| 2. 模型用 nexent 模型配置 | 不新增任何模型配置接口/表。前端下拉用现有 `GET /api/model/llm_list`；启动训练只传 `model_id`，服务端按租户从 `model_record_t` 解析 base_url / api_key / model_name；预检复用 `model_health_service.check_model_connectivity` |
| 3. 只写后端 | 交付 nexent 仓库内的后端代码 + SQL + 部署配置；前端改动清单写入原型文档由前端实现 |
| 4. 提供接口及交互 | 本文档 §5 + 交互原型（接口标注层） |

## 2. 架构

```
浏览器 → nexent-web(:3000, server.js 网关，cookie→JWT 注入)
            ├─ /api/model/llm_list ────────► nexent-config(:5010)   模型列表（现有，零改动）
            └─ /api/skillopt/* ───────────► nexent-skillopt(:5016)  本次新增服务
                                               │  skillopt_app 路由 → skillopt_service 业务
                                               │  ├─ 鉴权: auth_utils.get_current_user_id
                                               │  ├─ 模型解析: model_management_db (读 model_record_t)
                                               │  ├─ 预检: model_health_service.check_model_connectivity
                                               │  └─ 任务表: skillopt_job_t (nexent PostgreSQL)
                                               └─ subprocess ─ SkillOpt 引擎 scripts/train.py（零修改）
                                                  数据卷 /data/skillopt: uploads/ + jobs/<job_id>/{train.log, out/}
```

**为什么独立服务而不进 config/runtime 服务**：训练是长时任务且需要 SkillOpt 引擎的重依赖（独立 pip 依赖树），放进现有容器会污染依赖并互相影响资源；nexent 本身就是多服务架构（config 5010 / runtime 5014 / data-process 5012 …），新增 5016 完全遵循既有模式。服务间不需要 RPC——模型解析和任务表都直接走共享的 PostgreSQL（nexent 各服务共库是现状约定）。

## 3. 代码布局（全部在 nexent 仓库内）

```
backend/
├── apps/skillopt_app.py            # 路由层：APIRouter(prefix="/skillopt")，参数校验 + 委托 service
│                                   #   遵循 nexent App 层约定（JSONResponse {message,data}，HTTPException 报错）
├── services/skillopt_service.py    # 业务层：任务生命周期（Popen/killpg/断点续训/进度解析）、
│                                   #   上传校验（md/jsonl/zip/yaml）、模型解析与预检、配置组装
├── database/skillopt_db.py         # 数据层：skillopt_job_t CRUD（按 tenant_id 过滤）
├── database/db_models.py           # +class SkillOptJob（见 §4）
├── consts/model.py                 # +SkillOptStartRequest 等 Pydantic 模型
├── skillopt_service_main.py        # 服务入口：app_factory.create_app + uvicorn :5016
docker/
└── skillopt/Dockerfile             # python3.11 + SkillOpt 引擎源码(pin commit) + pip install + backend 代码
deploy/
├── docker/compose/…                # nexent-skillopt 服务定义 + 数据卷 + server.js 的 SKILLOPT_BACKEND 环境变量
└── sql/migrations/vX.X.X_add_skillopt.sql   # 建表 + 菜单权限行
```

SkillOpt 引擎（github.com/microsoft/SkillOpt，MIT）以固定 commit 进镜像 `/opt/skillopt`，**引擎零修改**，调用契约与其自带 WebUI 相同：`python scripts/train.py --config <yaml> --cfg-options k=v ...`。

## 4. 数据模型（nexent PostgreSQL）

```sql
CREATE TABLE nexent.skillopt_job_t (
    job_id        VARCHAR(64) PRIMARY KEY,          -- j_YYYYMMDD_HHMMSS_xxxx
    tenant_id     VARCHAR(100) NOT NULL,            -- 隔离键，全部查询强制过滤
    user_id       VARCHAR(100) NOT NULL,            -- 创建者（审计）
    name          VARCHAR(200),
    status        VARCHAR(20) NOT NULL,             -- running/succeeded/failed/stopped
    optimizer_model_id  INTEGER NOT NULL,           -- 引用 model_record_t.model_id
    target_model_id     INTEGER NOT NULL,
    params        JSONB,                            -- overrides + 上传 file_id + 切分参数快照（api_key 不入库）
    work_dir      VARCHAR(500) NOT NULL,            -- 数据卷内 jobs/<job_id>/
    pid           INTEGER,                          -- 运行中的训练进程组 id
    exit_code     INTEGER,
    error_msg     TEXT,
    best_score    NUMERIC,                          -- 结束时从 history.json 回填
    resume_count  INTEGER DEFAULT 0,
    create_time   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time   TIMESTAMP,
    delete_flag   VARCHAR(1) DEFAULT 'N'            -- 跟随 nexent 软删约定
);
CREATE INDEX idx_skillopt_job_tenant ON nexent.skillopt_job_t(tenant_id, create_time DESC);
```

文件不入库：上传文件与训练产物在服务数据卷 `/data/skillopt/`（uploads 按租户目录、jobs 按任务目录）。服务重启恢复逻辑：status=running 且 pid 已死 → 置 stopped（可续训）。

## 5. REST API（响应/错误全部按 nexent 约定）

成功 = `200 {"message":"ok","data":…}`；错误 = HTTPException（`{"detail":"可读原因"}`）。所有端点带 `Authorization` 头（网关自动注入）。

| # | 端点 | 说明 |
|---|---|---|
| 0 | `GET /api/model/llm_list` | **现有接口零改动**，前端模型下拉直接用 |
| 1 | `GET /skillopt/config/schema` | 表单字段元数据（六节 train/gradient/optimizer/evaluation…，类型/默认值/中文名/枚举）+ 默认值。SkillOpt 配置无 schema，这份白名单元数据是表单渲染与 YAML 校验的唯一依据 |
| 2 | `POST /skillopt/uploads/skill` | multipart .md ≤64KB，必填项 → `{file_id,name,size}` |
| 3 | `POST /skillopt/uploads/dataset` | type=raw：单个 JSON/JSONL（后续由引擎按 split_ratio/split_seed 确定性切分）→ `{file_id,items}`；type=split：zip 含 train/val/test/items.json → `{file_id,counts}`；结构不符 400 |
| 4 | `POST /skillopt/uploads/config` | 可选 YAML 覆盖，按 #1 白名单逐字段校验，非法 400 逐条列出 → `{file_id,valid_fields}` |
| 5 | `POST /skillopt/train/start` | body：`{name?, optimizer_model_id, target_model_id, overrides{}, uploads{skill, dataset{file_id,type,split_ratio?,split_seed?}, config_yaml?}}`。流程：① 校验必填与 file_id 归属租户 ② 按租户解析两个模型（不存在→404）③ 预检：connect_status 可用 + `check_model_connectivity` 现场探测（失败→400 可读原因）④ 该租户已有 running→409 ⑤ 组装配置并 Popen → `{job_id,status}` |
| 6 | `POST /skillopt/train/stop` | 停止当前租户运行中任务（killpg），状态置 stopped，产物保留可续训 |
| 7 | `GET /skillopt/train/status` | 当前租户运行中/最近任务：status、六阶段 stage、epoch/step、percent（无任务 data=null）|
| 8 | `GET /skillopt/train/logs?job_id&offset` | 增量日志（读 train.log，返回 next_offset）|
| 9 | `GET /skillopt/jobs?page&page_size` | 租户历史任务分页列表（含 best_score、模型名、创建人）|
| 10 | `GET /skillopt/jobs/{job_id}/best-skill` | best_skill.md 内容；`?download=1` 下载；跨租户 404 |
| 11 | `POST /skillopt/jobs/{job_id}/resume` | 断点续训：同 work_dir 重新拉起（引擎读 runtime_state.json 自动恢复）|

进度协议：解析训练 stdout 的既有标记 `[1/6 ROLLOUT]…[6/6 EVALUATE]`、`[EPOCH i/N]`、`[STEP g/T]`（正则移植自 SkillOpt 自带 WebUI）。

## 6. 模型集成细节（要求 2 的核心）

nexent `model_record_t` 的 LLM 记录 = `{model_name, model_repo, base_url, api_key, max_tokens}`，即 **OpenAI 兼容三元组**。映射到 SkillOpt 的 `openai_compatible` 后端，按角色注入训练子进程环境变量：

```
optimizer_backend=openai_compatible          target_backend=openai_compatible
OPTIMIZER_OPENAI_COMPATIBLE_BASE_URL=<base_url>   TARGET_OPENAI_COMPATIBLE_BASE_URL=…
OPTIMIZER_OPENAI_COMPATIBLE_API_KEY=<api_key>     TARGET_OPENAI_COMPATIBLE_API_KEY=…
OPTIMIZER_OPENAI_COMPATIBLE_MODEL=<repo/name>     TARGET_OPENAI_COMPATIBLE_MODEL=…
```

（环境变量名以 SkillOpt `.env.example` 为准，实现时核对；api_key 只进子进程环境，不写任务表、不出现在日志——沿用引擎的 `_redact_cfg` 脱敏。）Azure 专有部署等非 OpenAI 兼容形态暂不支持，预检时给出明确报错。

## 7. 训练执行与并发

- 每租户同时 1 个训练（409 拦截）；服务级并发上限 `SKILLOPT_MAX_CONCURRENT_JOBS`（默认 2，防多个租户同时打满资源）。
- 子进程 `start_new_session=True` 独立进程组；stdout 直接重定向到 `train.log`（服务重启不丢日志）。
- 断点续训 = 相同 work_dir 重新 Popen（SkillOpt 检测 runtime_state.json 自动恢复），resume 接口与 stop 后再 start 均可触达。

## 8. 待确认问题（评审时请拍板）

1. **任务环境 env.name**：SkillOpt 引擎要求每个训练绑定一个"环境适配器"（定义任务如何执行/打分）。取消 benchmark 预设后，默认用哪个适配器跑用户自有数据？方案 A（默认）：服务端配置默认 env（如 searchqa 型的通用问答适配器），高级 YAML 可覆盖；方案 B：一期先支持问答型任务，二期按 `envs/_template` 为你们的业务任务写自定义适配器。**建议 A，且把"自定义任务适配器"列为二期。**
2. 管理员是否需要查看/停止**全租户**任务（当前设计：仅本租户）？
3. 上传大小上限（暂定 skill 64KB / dataset 200MB / yaml 64KB）与保留策略（任务产物暂不自动清理）？
4. 菜单权限授予哪些角色（SQL 迁移里写 ADMIN + 普通用户，还是仅 ADMIN）？

## 9. 验证方案（实现阶段）

- pytest：fake train.py 夹具验证任务状态机/进度解析/续训/租户隔离（伪造两个 tenant 交叉访问全部 404）；上传校验（坏 zip、非法 YAML 字段）；模型解析 mock model_record_t。
- 集成冒烟：compose 起 postgres + skillopt 服务，curl 全接口；Swagger 与本文档核对。
- 交付分支：先推 helloworld3326/empty 评审，定稿后由你搬入公司 GitLab 的 nexent 仓库同分支。
