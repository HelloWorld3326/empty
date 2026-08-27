-- =============================================================================
-- SUPCON 数据智能体平台 一期 · 全量建表语句
-- PostgreSQL 15+
-- 契约文件，唯一真源。修改必须走 PR 并通知全员。
-- =============================================================================
-- 通用约定：
--   * tenant_id  一期恒为 'default'，预留多租户，不实现隔离逻辑
--   * deleted_at 软删标记，所有查询默认过滤 deleted_at IS NULL
--   * *_enc 后缀 应用层对称加密字段，前端一律脱敏回显
--   * 时间戳    统一 TIMESTAMPTZ，服务端写入 now()
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- =============================================================================
-- 一、身份与权限域            切片 S2 权限 / S3 登录与用户管理
-- =============================================================================

-- 用户
CREATE TABLE sys_user (
    user_id       BIGSERIAL     PRIMARY KEY,
    username      VARCHAR(64)   NOT NULL,
    password_hash VARCHAR(255)  NOT NULL,
    display_name  VARCHAR(64)   NOT NULL,
    employee_no   VARCHAR(32),
    email         VARCHAR(128),
    role          VARCHAR(16)   NOT NULL DEFAULT 'USER',   -- ADMIN | USER
    status        VARCHAR(16)   NOT NULL DEFAULT 'ACTIVE',  -- ACTIVE | DISABLED
    -- 预留外挂身份源（SSO / LDAP / CAS），一期不实现
    external_id   VARCHAR(128),
    source        VARCHAR(32)   NOT NULL DEFAULT 'LOCAL',   -- LOCAL | OAUTH | LDAP | CAS
    tenant_id     VARCHAR(64)   NOT NULL DEFAULT 'default',
    created_by    BIGINT,
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ   NOT NULL DEFAULT now(),
    deleted_at    TIMESTAMPTZ
);
CREATE UNIQUE INDEX uq_sys_user_username ON sys_user (tenant_id, username) WHERE deleted_at IS NULL;
CREATE INDEX idx_sys_user_tenant ON sys_user (tenant_id) WHERE deleted_at IS NULL;

-- 组（部门）
CREATE TABLE sys_group (
    group_id   BIGSERIAL   PRIMARY KEY,
    name       VARCHAR(64) NOT NULL,
    parent_id  BIGINT      REFERENCES sys_group (group_id),
    tenant_id  VARCHAR(64) NOT NULL DEFAULT 'default',
    created_by BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at TIMESTAMPTZ
);
CREATE INDEX idx_sys_group_parent ON sys_group (parent_id) WHERE deleted_at IS NULL;

-- 用户与组的多对多
CREATE TABLE sys_user_group (
    user_id    BIGINT      NOT NULL REFERENCES sys_user (user_id),
    group_id   BIGINT      NOT NULL REFERENCES sys_group (group_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, group_id)
);
CREATE INDEX idx_sys_user_group_group ON sys_user_group (group_id);

-- RBAC：角色能做什么。照搬 nexent 的三段式权限点
CREATE TABLE sys_role_permission (
    id                  BIGSERIAL   PRIMARY KEY,
    role                VARCHAR(16) NOT NULL,
    permission_category VARCHAR(32) NOT NULL,   -- agent | resource | record | user
    permission_type     VARCHAR(32) NOT NULL,   -- read | write | delete | publish
    permission_subtype  VARCHAR(32),            -- model | mcp | skill | knowledge ...
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_role_permission
    ON sys_role_permission (role, permission_category, permission_type, COALESCE(permission_subtype, ''));

-- =============================================================================
-- 二、智能体域                切片 S6 CRUD / S7 列表 / S10 挂载 / S23 记录
-- =============================================================================

-- 智能体版本表。复合主键 (agent_id, version_no)：
--   version_no = 0   草稿行，创作者编辑的就是它，可写
--   version_no >= 1  发布快照，不可变，永远不要 UPDATE
CREATE SEQUENCE agent_id_seq;

CREATE TABLE agent (
    agent_id           BIGINT       NOT NULL DEFAULT nextval('agent_id_seq'),
    version_no         INTEGER      NOT NULL DEFAULT 0,
    name               VARCHAR(64)  NOT NULL,
    description        TEXT         NOT NULL,
    icon_url           VARCHAR(512),
    -- 完整配置快照：
    -- { model: {model_id, name}, prompt, params:{max_tokens,temperature,top_p},
    --   context_policy: AUTO|THRESHOLD|NONE, retrieval_top_k }
    config             JSONB        NOT NULL DEFAULT '{}'::jsonb,
    -- DAC 可见性：PRIVATE 仅创建者 / READ_ONLY 组内只读 / EDIT 组内可编辑
    ingroup_permission VARCHAR(16)  NOT NULL DEFAULT 'PRIVATE',
    group_ids          BIGINT[]     NOT NULL DEFAULT '{}',
    tenant_id          VARCHAR(64)  NOT NULL DEFAULT 'default',
    created_by         BIGINT       NOT NULL,
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (agent_id, version_no)
);
CREATE INDEX idx_agent_draft ON agent (agent_id) WHERE version_no = 0;
CREATE INDEX idx_agent_name ON agent (tenant_id, name) WHERE version_no = 0;

-- 智能体的状态与线上版本指针。一个 agent_id 一行
CREATE TABLE agent_meta (
    agent_id            BIGINT      PRIMARY KEY,
    status              VARCHAR(16) NOT NULL DEFAULT 'DRAFT',  -- DRAFT | PUBLISHED | OFFLINE
    current_version_no  INTEGER,                               -- NULL 表示从未发布
    tenant_id           VARCHAR(64) NOT NULL DEFAULT 'default',
    created_by          BIGINT      NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at          TIMESTAMPTZ
);
CREATE INDEX idx_agent_meta_status ON agent_meta (tenant_id, status) WHERE deleted_at IS NULL;
CREATE INDEX idx_agent_meta_creator ON agent_meta (created_by) WHERE deleted_at IS NULL;

-- 资源挂载关系。随版本快照，发布时整体拷贝
CREATE TABLE agent_resource (
    agent_id      BIGINT      NOT NULL,
    version_no    INTEGER     NOT NULL,
    resource_type VARCHAR(16) NOT NULL,   -- MCP | SKILL | KNOWLEDGE
    resource_id   BIGINT      NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (agent_id, version_no, resource_type, resource_id)
);
CREATE INDEX idx_agent_resource_lookup ON agent_resource (resource_type, resource_id);

-- 发布记录。发布 / 上架 / 下架 / 保存草稿 都记一条
CREATE TABLE agent_publish_log (
    id              BIGSERIAL   PRIMARY KEY,
    agent_id        BIGINT      NOT NULL,
    version_no      INTEGER,
    action          VARCHAR(24) NOT NULL,   -- PUBLISH | UNPUBLISH | REPUBLISH | SAVE_DRAFT
    result          VARCHAR(16) NOT NULL,   -- SUCCESS | FAILED
    fail_reason     TEXT,
    visibility_desc VARCHAR(128),
    operator_id     BIGINT      NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_publish_log_agent ON agent_publish_log (agent_id, created_at DESC);

-- 说明：配置变更记录不单独建表。
-- 由相邻两个版本快照的 config JSONB 做 diff 实时生成，
-- 操作人与时间取自 agent 表该版本行的 created_by / created_at。

-- =============================================================================
-- 三、资源域        切片 S12 模型 / S13 MCP / S14 Skill / S15 知识库
-- =============================================================================

-- 模型（大模型 / ASR / TTS / Embedding）
CREATE TABLE resource_model (
    id            BIGSERIAL    PRIMARY KEY,
    name          VARCHAR(128) NOT NULL,   -- 调用时用的模型名，如 qwen-plus
    display_name  VARCHAR(128) NOT NULL,
    model_type    VARCHAR(16)  NOT NULL,   -- LLM | ASR | TTS | EMBEDDING
    provider      VARCHAR(64)  NOT NULL,   -- 如 dashscope
    base_url      VARCHAR(512) NOT NULL,
    api_key_enc   TEXT,                    -- 加密存储，接口脱敏返回
    status        VARCHAR(16)  NOT NULL DEFAULT 'ENABLED',  -- ENABLED | DISABLED
    health_status VARCHAR(16)  NOT NULL DEFAULT 'UNKNOWN',  -- OK | WARN | ERROR | UNKNOWN
    last_check_at TIMESTAMPTZ,
    tenant_id     VARCHAR(64)  NOT NULL DEFAULT 'default',
    created_by    BIGINT       NOT NULL,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    deleted_at    TIMESTAMPTZ
);
CREATE INDEX idx_model_type ON resource_model (tenant_id, model_type) WHERE deleted_at IS NULL;

-- MCP / API 工具服务
CREATE TABLE resource_mcp (
    id              BIGSERIAL    PRIMARY KEY,
    name            VARCHAR(128) NOT NULL,
    description     TEXT         NOT NULL,
    service_type    VARCHAR(32)  NOT NULL DEFAULT 'REMOTE',  -- REMOTE | BUSINESS_API
    url             VARCHAR(512) NOT NULL,
    auth_type       VARCHAR(24)  NOT NULL DEFAULT 'NONE',    -- NONE | API_KEY | BEARER | OAUTH2
    auth_secret_enc TEXT,
    owner           VARCHAR(64),                             -- 维护团队
    health_status   VARCHAR(16)  NOT NULL DEFAULT 'UNKNOWN', -- OK | WARN | ERROR | WATCHING
    last_check_at   TIMESTAMPTZ,
    last_error      TEXT,
    tenant_id       VARCHAR(64)  NOT NULL DEFAULT 'default',
    created_by      BIGINT       NOT NULL,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    deleted_at      TIMESTAMPTZ
);
CREATE INDEX idx_mcp_tenant ON resource_mcp (tenant_id) WHERE deleted_at IS NULL;

-- 从 MCP 服务解析出来的工具清单，接入与测试时刷新
CREATE TABLE resource_mcp_tool (
    id           BIGSERIAL    PRIMARY KEY,
    mcp_id       BIGINT       NOT NULL REFERENCES resource_mcp (id),
    tool_name    VARCHAR(128) NOT NULL,
    description  TEXT,
    input_schema JSONB        NOT NULL DEFAULT '{}'::jsonb,  -- 直接转 function calling schema
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_mcp_tool ON resource_mcp_tool (mcp_id, tool_name);

-- Skill = 结构化 Prompt 模板（不是可执行代码，无沙箱）
CREATE TABLE resource_skill (
    id            BIGSERIAL    PRIMARY KEY,
    name          VARCHAR(128) NOT NULL,
    description   TEXT         NOT NULL,
    output_format VARCHAR(24)  NOT NULL,   -- JSON | TABLE | TEXT
    template_body TEXT         NOT NULL,   -- 支持 {{var}} 占位
    input_vars    JSONB        NOT NULL DEFAULT '[]'::jsonb,  -- [{name,label,required,example}]
    version       INTEGER      NOT NULL DEFAULT 1,
    health_status VARCHAR(16)  NOT NULL DEFAULT 'UNKNOWN',    -- 取自最近一次试运行
    last_test_at  TIMESTAMPTZ,
    last_test_msg TEXT,
    owner         VARCHAR(64),
    tenant_id     VARCHAR(64)  NOT NULL DEFAULT 'default',
    created_by    BIGINT       NOT NULL,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    deleted_at    TIMESTAMPTZ
);
CREATE INDEX idx_skill_tenant ON resource_skill (tenant_id) WHERE deleted_at IS NULL;

-- 知识库：只登记元信息，检索走内网 KnowledgeProvider
CREATE TABLE resource_knowledge (
    id              BIGSERIAL    PRIMARY KEY,
    external_kb_id  VARCHAR(128) NOT NULL,   -- 内网知识库的 ID
    name            VARCHAR(128) NOT NULL,
    owner           VARCHAR(64),
    doc_count       INTEGER,                 -- 拿不到则 NULL，前端显示 —
    doc_size_bytes  BIGINT,
    last_sync_at    TIMESTAMPTZ,
    tenant_id       VARCHAR(64)  NOT NULL DEFAULT 'default',
    created_by      BIGINT       NOT NULL,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    deleted_at      TIMESTAMPTZ
);
CREATE UNIQUE INDEX uq_knowledge_external ON resource_knowledge (tenant_id, external_kb_id) WHERE deleted_at IS NULL;

-- =============================================================================
-- 四、会话与运行域   切片 S16 持久化 / S20 反馈 / S21 附件 / S22 记录
-- =============================================================================

CREATE TABLE conversation (
    id                BIGSERIAL    PRIMARY KEY,
    title             VARCHAR(64)  NOT NULL,
    agent_id          BIGINT       NOT NULL,
    agent_version_no  INTEGER      NOT NULL,
    user_id           BIGINT       NOT NULL,
    tenant_id         VARCHAR(64)  NOT NULL DEFAULT 'default',
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    deleted_at        TIMESTAMPTZ
);
CREATE INDEX idx_conv_user ON conversation (user_id, updated_at DESC) WHERE deleted_at IS NULL;

CREATE TABLE message (
    id              BIGSERIAL    PRIMARY KEY,
    conversation_id BIGINT       NOT NULL REFERENCES conversation (id),
    role            VARCHAR(16)  NOT NULL,   -- USER | ASSISTANT
    content         TEXT         NOT NULL,
    run_id          BIGINT,                  -- assistant 消息关联的运行
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX idx_message_conv ON message (conversation_id, id);

-- 一次运行。即时测试与正式问答共用，靠 mode 区分
CREATE TABLE run (
    id              BIGSERIAL    PRIMARY KEY,
    conversation_id BIGINT,                  -- mode=TEST 时为 NULL
    agent_id        BIGINT       NOT NULL,
    version_no      INTEGER      NOT NULL,
    user_id         BIGINT       NOT NULL,
    mode            VARCHAR(8)   NOT NULL,   -- CHAT | TEST
    status          VARCHAR(16)  NOT NULL DEFAULT 'RUNNING', -- RUNNING|SUCCESS|CANCELLED|FAILED
    duration_ms     INTEGER,
    error_code      VARCHAR(48),
    error_message   TEXT,
    tenant_id       VARCHAR(64)  NOT NULL DEFAULT 'default',
    started_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ
);
CREATE INDEX idx_run_agent ON run (agent_id, started_at DESC);
CREATE INDEX idx_run_conv ON run (conversation_id);

-- 完整事件轨迹。对话详情按 seq 回放，不需要额外快照表
CREATE TABLE run_event (
    id         BIGSERIAL   PRIMARY KEY,
    run_id     BIGINT      NOT NULL REFERENCES run (id),
    seq        INTEGER     NOT NULL,
    event_type VARCHAR(32) NOT NULL,   -- 见 sse-protocol.md 的 9 类
    payload    JSONB       NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_run_event_seq ON run_event (run_id, seq);

CREATE TABLE citation (
    id          BIGSERIAL    PRIMARY KEY,
    run_id      BIGINT       NOT NULL REFERENCES run (id),
    message_id  BIGINT,
    source_type VARCHAR(16)  NOT NULL,   -- KNOWLEDGE | TOOL
    source_name VARCHAR(128) NOT NULL,
    title       VARCHAR(256),
    snippet     TEXT,
    ref         VARCHAR(512),           -- 原文定位（doc_id / url / 调用 id）
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX idx_citation_run ON citation (run_id);

CREATE TABLE feedback (
    id         BIGSERIAL   PRIMARY KEY,
    message_id BIGINT      NOT NULL REFERENCES message (id),
    user_id    BIGINT      NOT NULL,
    helpful    BOOLEAN     NOT NULL,
    -- 无帮助时的问题类型多选：INACCURATE|ILLOGICAL|SHALLOW|UNACTIONABLE|SLOW
    tags       JSONB       NOT NULL DEFAULT '[]'::jsonb,
    comment    TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_feedback_message_user ON feedback (message_id, user_id);

CREATE TABLE attachment (
    id              BIGSERIAL    PRIMARY KEY,
    conversation_id BIGINT,
    message_id      BIGINT,
    filename        VARCHAR(255) NOT NULL,
    size_bytes      BIGINT       NOT NULL,
    mime            VARCHAR(128) NOT NULL,
    storage_path    VARCHAR(512) NOT NULL,   -- 本地卷相对路径，一期不引入对象存储
    uploaded_by     BIGINT       NOT NULL,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    deleted_at      TIMESTAMPTZ
);
CREATE INDEX idx_attachment_conv ON attachment (conversation_id) WHERE deleted_at IS NULL;
