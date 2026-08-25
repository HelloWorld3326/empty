"""Schema 召回。

**这是整个方案里最该被替换的一块，所以做成了接口。**

表少（<50 张）且注释齐全时，最好的策略是全量塞进 prompt，召回纯属画蛇添足；
表多到几百上千张时，召回质量直接决定问数准确率的上限。在拿到真实库规模
之前，默认实现是关键词版——依赖少、可解释、出错时人能看懂为什么。

要换成向量召回，实现 :class:`SchemaRetriever` 再在 ``build_retriever`` 里
挂上即可，调用方一行不用改。换之前请先用 evalkit 跑一遍基线，
否则你不知道换了到底是变好还是变坏。
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections import Counter
from typing import ClassVar

from .introspect import SchemaCache, TableInfo

_CJK = re.compile(r"[一-鿿]")
_WORD = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> Counter[str]:
    """中英混合分词。

    中文按二元字组切（不引分词库，业务表名里的中文本来就短），
    英文按下划线/大小写边界切。够用，且没有额外依赖。
    """
    text = text.lower()
    tokens: list[str] = []
    tokens.extend(_WORD.findall(text))
    cjk_runs = _CJK.findall(text)
    cjk_text = "".join(ch for ch in text if _CJK.match(ch))
    tokens.extend(cjk_runs)
    tokens.extend(cjk_text[i : i + 2] for i in range(len(cjk_text) - 1))
    return Counter(tokens)


class SchemaRetriever(ABC):
    """给定自然语言问题，返回最相关的若干张表。"""

    @abstractmethod
    def search(
        self, query: str, *, datasource: str | None = None, limit: int = 20
    ) -> list[TableInfo]:
        ...


class KeywordSchemaRetriever(SchemaRetriever):
    """关键词打分召回。

    加权逻辑：表名 > 表注释 > 字段名 > 字段注释。表名命中通常是最强信号
    （用户说「订单」，``dwd_order_df`` 就该排前面），字段注释权重最低是因为
    它往往很长，容易靠体量刷分。
    """

    _WEIGHTS: ClassVar[dict[str, float]] = {
        "table_name": 3.0,
        "alias": 3.0,
        "table_comment": 2.0,
        "column_name": 1.0,
        "column_comment": 0.6,
    }

    def __init__(
        self,
        cache: SchemaCache,
        *,
        aliases: dict[str, dict[str, list[str]]] | None = None,
        small_schema_threshold: int = 60,
    ) -> None:
        """
        :param aliases: ``{数据源: {表名: [业务别名, ...]}}``。中文提问打英文表名
            是国内业务库的常态，别名是最省事的补救手段，见 ``_alias_tokens``。
        :param small_schema_threshold: 表数量不超过这个值时，召回不足会退化成
            返回全部表。表少的时候「全量塞进 prompt」本来就是最优解，
            召回在这种规模下只会帮倒忙。
        """
        self._cache = cache
        self._aliases = aliases or {}
        self._small_schema_threshold = small_schema_threshold
        self._index: dict[str, list[tuple[TableInfo, dict[str, Counter[str]]]]] = {}

    def _alias_tokens(self, table: TableInfo) -> Counter[str]:
        per_ds = self._aliases.get(table.datasource, {})
        terms = per_ds.get(table.name, []) + per_ds.get(table.qualified_name, [])
        return _tokenize(" ".join(terms))

    def _fields_for(self, table: TableInfo) -> dict[str, Counter[str]]:
        return {
            "table_name": _tokenize(table.qualified_name),
            "alias": self._alias_tokens(table),
            "table_comment": _tokenize(table.comment or ""),
            "column_name": _tokenize(" ".join(c.name for c in table.columns)),
            "column_comment": _tokenize(" ".join(c.comment or "" for c in table.columns)),
        }

    def _ensure_index(
        self, datasource: str | None
    ) -> list[tuple[TableInfo, dict[str, Counter[str]]]]:
        key = datasource or "*"
        if key not in self._index:
            self._index[key] = [
                (t, self._fields_for(t)) for t in self._cache.tables(datasource)
            ]
        return self._index[key]

    def invalidate(self) -> None:
        self._index.clear()

    def search(
        self, query: str, *, datasource: str | None = None, limit: int = 20
    ) -> list[TableInfo]:
        q = _tokenize(query)
        if not q:
            return []
        scored: list[tuple[float, TableInfo]] = []
        for table, fields in self._ensure_index(datasource):
            score = 0.0
            for field_name, weight in self._WEIGHTS.items():
                field_tokens = fields[field_name]
                hits = sum((field_tokens[tok] and 1) or 0 for tok in q)
                if hits:
                    # 用命中的 token 种类数而不是频次，避免长文本刷分。
                    score += weight * hits
            if score > 0:
                scored.append((score, table))
        scored.sort(key=lambda pair: (-pair[0], pair[1].qualified_name))
        hits = [t for _, t in scored[:limit]]

        # 关键词召回在「中文提问 + 英文表名 + 无注释」时会直接归零，
        # 而这恰恰是国内业务库最常见的样子。表不多的时候，宁可全给模型看，
        # 也不要让它拿着一个空列表去猜表名——猜出来的 SQL 跑得通但结果是错的。
        candidates = self._ensure_index(datasource)
        if len(hits) < limit and len(candidates) <= self._small_schema_threshold:
            seen = {t.qualified_name for t in hits}
            for table, _fields in candidates:
                if len(hits) >= limit:
                    break
                if table.qualified_name not in seen:
                    hits.append(table)
        return hits


def build_retriever(
    cache: SchemaCache,
    kind: str = "keyword",
    *,
    aliases: dict[str, dict[str, list[str]]] | None = None,
) -> SchemaRetriever:
    if kind == "keyword":
        return KeywordSchemaRetriever(cache, aliases=aliases)
    raise ValueError(
        f"未知的 schema 召回实现: {kind}。"
        "向量召回请实现 SchemaRetriever 后在这里注册，并先用 evalkit 跑基线对比。"
    )
