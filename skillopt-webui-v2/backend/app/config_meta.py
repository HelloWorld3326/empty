"""配置表单元数据 — 前端动态渲染表单的唯一数据源。

SkillOpt 原始配置是无 schema 的 YAML（skillopt/config.py 为纯 dict），这里为
`configs/_base_/default.yaml` 的字段补上类型/默认值/中文说明/枚举/密钥标记，
接口 GET /api/configs/{name} 会把该元数据与解析后的配置一起返回。

前端渲染规则见 docs/FRONTEND_REQUIREMENTS_zh.md：
  - 按 section 分组渲染折叠面板，组内按定义顺序排列；
  - type: int/float → 数字输入；bool → 开关；enum 存在 → 下拉；str → 文本；
  - secret=True → 密码框（回显值为 ****** 时表示已配置、未修改则不提交该字段）；
  - advanced=True → 收进"高级"折叠区；
  - 元数据未覆盖的字段（各 benchmark 的 env 透传键）→ 高级 YAML 编辑器兜底。
"""
from __future__ import annotations

from typing import Any

# 每项: (section, key, type, label_zh, help_zh, enum, secret, advanced)
_F = "field"


def _field(
    section: str,
    key: str,
    type_: str,
    label_zh: str,
    help_zh: str = "",
    enum: list | None = None,
    secret: bool = False,
    advanced: bool = False,
) -> dict[str, Any]:
    return {
        "section": section,
        "key": key,
        "type": type_,
        "label_zh": label_zh,
        "help_zh": help_zh,
        "enum": enum,
        "secret": secret,
        "advanced": advanced,
    }


FIELD_SPECS: list[dict[str, Any]] = [
    # ── model ────────────────────────────────────────────────────────────
    _field("model", "backend", "str", "模型后端",
           "共享后端类型", enum=["azure_openai", "openai_compatible", "qwen", "minimax", "claude", "codex"]),
    _field("model", "optimizer", "str", "优化器模型", "执行技能编辑的模型（如 gpt-5.5）"),
    _field("model", "target", "str", "目标模型", "被优化技能所服务的执行模型"),
    _field("model", "optimizer_backend", "str", "优化器后端",
           enum=["openai_chat", "qwen_chat", "minimax_chat", "claude_chat", "codex_exec", "claude_code_exec"]),
    _field("model", "target_backend", "str", "目标后端",
           enum=["openai_chat", "qwen_chat", "minimax_chat", "claude_chat", "codex_exec", "claude_code_exec"]),
    _field("model", "reasoning_effort", "str", "推理强度", enum=["low", "medium", "high"]),
    _field("model", "azure_openai_endpoint", "str", "Azure OpenAI Endpoint",
           "如 https://your-resource.openai.azure.com/；留空则回退环境变量 AZURE_OPENAI_ENDPOINT"),
    _field("model", "azure_openai_api_version", "str", "Azure API 版本", advanced=True),
    _field("model", "azure_openai_api_key", "str", "Azure OpenAI API Key",
           "留空则回退环境变量 AZURE_OPENAI_API_KEY", secret=True),
    _field("model", "azure_openai_auth_mode", "str", "Azure 认证方式",
           "留空回退环境变量（默认 azure_cli）", enum=["", "api_key", "azure_cli", "managed_identity", "openai_compatible"],
           advanced=True),
    _field("model", "qwen_chat_base_url", "str", "Qwen 服务地址",
           "OpenAI 兼容的 vLLM/Qwen 服务，如 http://localhost:8000/v1", advanced=True),
    _field("model", "qwen_chat_api_key", "str", "Qwen API Key", secret=True, advanced=True),
    _field("model", "minimax_base_url", "str", "MiniMax 服务地址", "留空默认 https://api.minimax.io/v1", advanced=True),
    _field("model", "minimax_api_key", "str", "MiniMax API Key", secret=True, advanced=True),
    _field("model", "minimax_model", "str", "MiniMax 模型名", advanced=True),
    # ── train ────────────────────────────────────────────────────────────
    _field("train", "num_epochs", "int", "训练轮数 (Epochs)", "完整遍历训练集的轮数"),
    _field("train", "batch_size", "int", "批大小 (Batch Size)", "每步(step)训练的任务数"),
    _field("train", "train_size", "int", "训练集大小", "0 = 按数据集划分自动推导", advanced=True),
    _field("train", "accumulation", "int", "梯度累积", advanced=True),
    _field("train", "seed", "int", "随机种子", advanced=True),
    # ── gradient ─────────────────────────────────────────────────────────
    _field("gradient", "minibatch_size", "int", "反思小批大小", "每次反思(reflect)处理的轨迹数"),
    _field("gradient", "merge_batch_size", "int", "聚合批大小", advanced=True),
    _field("gradient", "analyst_workers", "int", "反思并行数", "并行执行反思分析的 worker 数"),
    _field("gradient", "max_analyst_rounds", "int", "最大反思轮数", advanced=True),
    _field("gradient", "failure_only", "bool", "仅反思失败样本", advanced=True),
    # ── optimizer ────────────────────────────────────────────────────────
    _field("optimizer", "learning_rate", "int", "学习率（每步最大编辑数）",
           "SkillOpt 的 DL 类比：每步允许对技能文档做的最大增删改条数(edit budget)"),
    _field("optimizer", "min_learning_rate", "int", "最小学习率", "衰减调度器的下限", advanced=True),
    _field("optimizer", "lr_scheduler", "str", "学习率调度器",
           enum=["constant", "linear", "cosine", "autonomous"]),
    _field("optimizer", "lr_control_mode", "str", "学习率控制模式",
           enum=["fixed", "autonomous", "none"], advanced=True),
    _field("optimizer", "skill_update_mode", "str", "技能更新模式",
           enum=["patch", "rewrite_from_suggestions", "full_rewrite_minibatch"]),
    _field("optimizer", "use_slow_update", "bool", "慢更新", "epoch 边界的动量式整合更新"),
    _field("optimizer", "slow_update_samples", "int", "慢更新采样数", advanced=True),
    _field("optimizer", "longitudinal_pair_policy", "str", "纵向配对策略",
           enum=["mixed", "changed", "unchanged"], advanced=True),
    _field("optimizer", "use_meta_skill", "bool", "元技能", "跨 epoch 的优化器记忆"),
    _field("optimizer", "use_skill_aware_reflection", "bool", "技能感知反思", advanced=True),
    # ── evaluation ───────────────────────────────────────────────────────
    _field("evaluation", "use_gate", "bool", "验证门控 (Gate)",
           "候选编辑仅在严格改进验证分数时才被接受"),
    _field("evaluation", "sel_env_num", "int", "验证集大小", "0 = 使用全部", advanced=True),
    _field("evaluation", "test_env_num", "int", "测试集大小", "0 = 使用全部", advanced=True),
    _field("evaluation", "eval_test", "bool", "训练后评测测试集", advanced=True),
    # ── env ──────────────────────────────────────────────────────────────
    _field("env", "name", "str", "Benchmark 名称", "由预设决定，一般无需修改"),
    _field("env", "skill_init", "str", "初始技能文件", "技能种子 .md 的仓库相对路径"),
    _field("env", "split_mode", "str", "数据划分方式", enum=["ratio", "split_dir"], advanced=True),
    _field("env", "split_dir", "str", "预划分数据目录", advanced=True),
    _field("env", "data_path", "str", "数据集路径", advanced=True),
    _field("env", "exec_timeout", "int", "单次调用超时(秒)", advanced=True),
    # env.out_root 由服务端强制指定为任务目录，不暴露给表单。
]

SECTIONS = [
    {"name": "model", "label_zh": "模型与后端"},
    {"name": "train", "label_zh": "训练参数"},
    {"name": "gradient", "label_zh": "反思与聚合"},
    {"name": "optimizer", "label_zh": "优化器"},
    {"name": "evaluation", "label_zh": "评估与门控"},
    {"name": "env", "label_zh": "环境与数据"},
]


def form_schema() -> dict:
    """返回前端动态表单 schema：分组 + 字段元数据。"""
    return {"sections": SECTIONS, "fields": FIELD_SPECS}


_KNOWN = {(f["section"], f["key"]) for f in FIELD_SPECS}


def split_known_unknown(cfg: dict) -> tuple[dict, dict]:
    """把结构化配置分成（元数据已覆盖的字段值, 未覆盖的其余字段）。

    未覆盖部分交给前端的"高级 YAML 编辑器"兜底展示/编辑。
    """
    known: dict[str, dict] = {}
    unknown: dict[str, dict] = {}
    for section, values in cfg.items():
        if not isinstance(values, dict):
            unknown[section] = values
            continue
        for key, value in values.items():
            if (section, key) in _KNOWN:
                known.setdefault(section, {})[key] = value
            else:
                unknown.setdefault(section, {})[key] = value
    return known, unknown
