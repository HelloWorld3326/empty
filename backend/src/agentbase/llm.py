"""LLM 工厂。

默认走阿里云百炼的 OpenAI 兼容端点（``/compatible-mode/v1``），用
``ChatOpenAI`` 直连即可。换成专有云部署或别的国产模型，只改 base_url/model。

注意：兼容端点对 OpenAI 的部分参数支持不全，凡是非必需参数一律按需下发，
不要无脑透传，否则会被服务端以 400 拒绝。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI

from .config import LLMConfig


def build_chat_model(cfg: LLMConfig, *, streaming: bool = True) -> ChatOpenAI:
    if not cfg.api_key:
        raise RuntimeError(
            "LLM api_key 为空。请在 .env 里设置 DASHSCOPE_API_KEY，"
            "并确认 config.yaml 的 llm.api_key 写的是 ${DASHSCOPE_API_KEY}"
        )
    return ChatOpenAI(
        model=cfg.model,
        base_url=cfg.base_url,
        api_key=cfg.api_key,
        temperature=cfg.temperature,
        max_tokens=cfg.max_tokens,
        timeout=cfg.timeout_seconds,
        streaming=streaming,
        # 流式下拿 usage，用来观测 token 成本；兼容端点支持这个选项。
        stream_usage=True,
    )


def bind_tools(
    model: BaseChatModel, tools: Sequence[BaseTool], cfg: LLMConfig
) -> Any:
    """绑定工具。

    ``parallel_tool_calls`` 默认关闭：国内模型对并行工具调用的支持参差不齐，
    开着容易出现「返回了两个 tool_call 但第二个参数是幻觉」。等模型体检
    （见 scripts/model_checkup.py）通过后再在 config 里打开。
    """
    kwargs: dict[str, Any] = {}
    if cfg.parallel_tool_calls:
        kwargs["parallel_tool_calls"] = True
    return model.bind_tools(list(tools), **kwargs)
