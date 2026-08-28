"""脱敏工具。

数据会被送到云端大模型，所以离开本服务之前先把敏感字段掩码。
业务上确实需要展示完整值的字段（例如订单号），不要放进脱敏列表。
"""
import re

_PHONE = re.compile(r"(?<!\d)(1[3-9]\d)(\d{4})(\d{4})(?!\d)")
_ID_CARD = re.compile(r"(?<![0-9A-Za-z])(\d{6})(\d{8})(\d{3}[0-9Xx])(?![0-9A-Za-z])")
_BANK_CARD = re.compile(r"(?<!\d)(\d{4})(\d{8,11})(\d{4})(?!\d)")
_EMAIL = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*(@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")

# 这些字段整体掩码，不做正则匹配
SENSITIVE_KEYS = {"phone", "mobile", "id_card", "id_no", "bank_card", "card_no", "email", "address"}


def mask_text(text: str) -> str:
    text = _ID_CARD.sub(lambda m: f"{m.group(1)}********{m.group(3)}", text)
    text = _BANK_CARD.sub(lambda m: f"{m.group(1)}{'*' * len(m.group(2))}{m.group(3)}", text)
    text = _PHONE.sub(lambda m: f"{m.group(1)}****{m.group(3)}", text)
    text = _EMAIL.sub(lambda m: f"{m.group(1)}***{m.group(2)}", text)
    return text


def mask_value(key: str, value):
    if isinstance(value, str):
        if key.lower() in SENSITIVE_KEYS:
            return mask_text(value) if len(value) > 4 else "****"
        return mask_text(value)
    return value


def mask_payload(payload):
    """递归脱敏 dict / list 结构。"""
    if isinstance(payload, dict):
        return {k: mask_payload(v) if isinstance(v, (dict, list)) else mask_value(k, v) for k, v in payload.items()}
    if isinstance(payload, list):
        return [mask_payload(item) for item in payload]
    return payload
