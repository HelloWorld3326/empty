"""MaxKB 客户端。

两种对接方式：
  native —— 原生接口，流程是 打开会话拿 chat_id → 带 chat_id 发消息。
            需要查客户数据时必须用它，因为 chat_id 是身份绑定的钥匙。
  openai —— OpenAI 兼容接口，一次请求搞定，适合纯知识问答。

不同 MaxKB 版本的接口路径有差异，路径都做成了可配置项（见 config.py），
第一次对接时对着你部署版本自带的 API 文档核一遍即可，不用改代码。
"""
from __future__ import annotations

import json
import logging
from typing import AsyncIterator

import httpx

from .config import settings

logger = logging.getLogger("chat-gateway.maxkb")


class MaxKBError(RuntimeError):
    pass


class MaxKBClient:
    def __init__(self) -> None:
        self._base = settings.maxkb_base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        # MaxKB 应用 API Key。放在服务端，绝不下发给浏览器。
        return {
            "Authorization": f"Bearer {settings.maxkb_api_key}",
            "AUTHORIZATION": settings.maxkb_api_key,  # 兼容部分版本的非标准头
            "Content-Type": "application/json",
        }

    async def open_chat(self, variables: dict | None = None) -> str:
        """打开一个新会话，返回 chat_id。"""
        if settings.maxkb_mode == "openai":
            raise MaxKBError("openai 模式没有会话概念，请把 MAXKB_MODE 设为 native")

        app_id = settings.maxkb_app_id
        if not app_id:
            app_id = await self._fetch_app_id()

        url = self._base + settings.maxkb_path_open.format(app_id=app_id)
        async with httpx.AsyncClient(timeout=settings.maxkb_timeout) as client:
            try:
                resp = await client.get(url, headers=self._headers())
            except httpx.HTTPError as exc:
                raise MaxKBError(f"连接 MaxKB 失败: {exc}") from exc
            if resp.status_code >= 400:
                raise MaxKBError(f"打开会话失败 {resp.status_code}: {resp.text[:200]}")
            body = resp.json()
        chat_id = body.get("data") if isinstance(body.get("data"), str) else (body.get("data") or {}).get("chat_id")
        if not chat_id:
            raise MaxKBError(f"未能从返回中解析 chat_id: {str(body)[:200]}")
        return chat_id

    async def _fetch_app_id(self) -> str:
        url = self._base + settings.maxkb_path_profile
        async with httpx.AsyncClient(timeout=settings.maxkb_timeout) as client:
            try:
                resp = await client.get(url, headers=self._headers())
            except httpx.HTTPError as exc:
                raise MaxKBError(f"连接 MaxKB 失败: {exc}") from exc
            if resp.status_code >= 400:
                raise MaxKBError(f"获取应用信息失败 {resp.status_code}: {resp.text[:200]}")
            data = (resp.json() or {}).get("data") or {}
        app_id = data.get("id")
        if not app_id:
            raise MaxKBError("应用信息里没有 id，请检查 MAXKB_API_KEY 是否正确")
        return app_id

    async def stream_chat(self, chat_id: str, question: str, variables: dict | None = None) -> AsyncIterator[str]:
        """流式对话，逐段吐出正文文本。"""
        if settings.maxkb_mode == "openai":
            async for chunk in self._stream_openai(question):
                yield chunk
            return

        url = self._base + settings.maxkb_path_message.format(chat_id=chat_id)
        payload = {"message": question, "re_chat": False, "stream": True}
        if variables:
            # 工作流里可以取到这些自定义变量（例如 tool_token）
            payload["form_data"] = variables

        async for text in self._stream(url, payload):
            yield text

    async def _stream_openai(self, question: str) -> AsyncIterator[str]:
        url = self._base + settings.maxkb_path_openai
        payload = {
            "model": settings.maxkb_app_id or "maxkb",
            "messages": [{"role": "user", "content": question}],
            "stream": True,
        }
        async for text in self._stream(url, payload):
            yield text

    async def _stream(self, url: str, payload: dict) -> AsyncIterator[str]:
        try:
            async with httpx.AsyncClient(timeout=settings.maxkb_timeout) as client:
                async with client.stream("POST", url, headers=self._headers(), json=payload) as resp:
                    if resp.status_code >= 400:
                        detail = (await resp.aread()).decode("utf-8", "ignore")
                        raise MaxKBError(f"对话失败 {resp.status_code}: {detail[:200]}")
                    async for text in _iter_sse_content(resp):
                        yield text
        except httpx.HTTPError as exc:
            raise MaxKBError(f"连接 MaxKB 失败: {exc}") from exc


async def _iter_sse_content(resp: httpx.Response) -> AsyncIterator[str]:
    """解析 SSE。同时兼容 MaxKB 原生格式和 OpenAI 格式的增量字段。"""
    async for line in resp.aiter_lines():
        line = line.strip()
        if not line or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data in {"", "[DONE]"}:
            continue
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            yield data
            continue
        text = _extract_text(event)
        if text:
            yield text


def _extract_text(event: dict) -> str:
    # OpenAI 格式：choices[0].delta.content
    choices = event.get("choices")
    if isinstance(choices, list) and choices:
        delta = choices[0].get("delta") or choices[0].get("message") or {}
        return delta.get("content") or ""
    # MaxKB 原生格式：content
    if isinstance(event.get("content"), str):
        return event["content"]
    data = event.get("data")
    if isinstance(data, dict) and isinstance(data.get("content"), str):
        return data["content"]
    return ""


client = MaxKBClient()
