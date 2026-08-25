"""Skill 加载器 —— 这就是平台上「创建 agent」的入口。

一个 skill 就是一个目录加一个 ``SKILL.md``：

    skills/
      指标口径/
        SKILL.md          <- frontmatter + 正文
        指标明细.md        <- 附属资料，正文里可以引用

**渐进式加载**是这里唯一重要的设计：system prompt 里只放每个 skill 的
name 和一句话 description（几十 token），模型判断需要时才调 ``read_skill``
把全文拉进上下文。这是平台能挂上百个 skill 而不撑爆上下文的唯一办法，
也是「业务同学随便写、写多长都行」这件事成立的前提。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)
_NAME_OK = re.compile(r"^[\w一-鿿][-\w一-鿿]*$")


class SkillError(ValueError):
    pass


@dataclass
class Skill:
    name: str
    description: str
    body: str
    path: Path
    allowed_tools: list[str] | None = None
    datasources: list[str] | None = None

    @property
    def directory(self) -> Path:
        return self.path.parent


def parse_skill(path: Path, *, max_description_chars: int = 200) -> Skill:
    text = path.read_text(encoding="utf-8")
    match = _FRONTMATTER.match(text)
    if not match:
        raise SkillError(
            f"{path}: 缺少 frontmatter。文件必须以 --- 开头，"
            "至少包含 name 和 description 两个字段。"
        )
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise SkillError(f"{path}: frontmatter 不是合法 YAML: {exc}") from exc
    if not isinstance(meta, dict):
        raise SkillError(f"{path}: frontmatter 必须是键值对")

    name = str(meta.get("name") or "").strip()
    description = str(meta.get("description") or "").strip()
    if not name:
        raise SkillError(f"{path}: frontmatter 缺少 name")
    if not _NAME_OK.match(name):
        raise SkillError(f"{path}: name `{name}` 不合法，只允许中英文、数字、下划线和连字符")
    if not description:
        # description 是模型决定要不要加载这个 skill 的唯一依据，
        # 空描述等于这个 skill 永远不会被用到。
        raise SkillError(f"{path}: frontmatter 缺少 description —— 模型靠它判断何时使用本 skill")
    if len(description) > max_description_chars:
        raise SkillError(
            f"{path}: description 超过 {max_description_chars} 字。"
            "它会常驻 system prompt，请压缩成一句话，细节写进正文。"
        )

    return Skill(
        name=name,
        description=description,
        body=match.group(2).strip(),
        path=path,
        allowed_tools=_str_list(meta.get("allowed_tools")),
        datasources=_str_list(meta.get("datasources")),
    )


def _str_list(value) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


class SkillRegistry:
    def __init__(self, paths: list[str], *, max_description_chars: int = 200) -> None:
        self._paths = [Path(p) for p in paths]
        self._max_description_chars = max_description_chars
        self._skills: dict[str, Skill] = {}
        self._errors: list[str] = []

    def reload(self) -> tuple[int, list[str]]:
        """重新扫描。返回 (加载成功数, 错误列表)。

        单个 skill 写错不该让整个平台起不来——业务同学会写错，这是常态。
        错误收集起来在 UI 上展示，其余 skill 正常工作。
        """
        skills: dict[str, Skill] = {}
        errors: list[str] = []
        for root in self._paths:
            if not root.exists():
                errors.append(f"skill 目录不存在: {root}")
                continue
            for skill_file in sorted(root.rglob("SKILL.md")):
                try:
                    skill = parse_skill(
                        skill_file, max_description_chars=self._max_description_chars
                    )
                except SkillError as exc:
                    errors.append(str(exc))
                    continue
                if skill.name in skills:
                    errors.append(
                        f"skill 名字冲突: `{skill.name}` 同时出现在 "
                        f"{skills[skill.name].path} 和 {skill.path}"
                    )
                    continue
                skills[skill.name] = skill
        self._skills, self._errors = skills, errors
        return len(skills), errors

    @property
    def errors(self) -> list[str]:
        return list(self._errors)

    def all(self) -> list[Skill]:
        return sorted(self._skills.values(), key=lambda s: s.name)

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def catalog_for_prompt(self) -> str:
        """注入 system prompt 的目录。只有名字和描述，没有正文。"""
        if not self._skills:
            return "(当前没有可用的 skill)"
        return "\n".join(f"- {s.name}: {s.description}" for s in self.all())
