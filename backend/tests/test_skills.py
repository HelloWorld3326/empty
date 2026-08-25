from __future__ import annotations

from pathlib import Path

import pytest

from agentbase.skillsys.loader import SkillError, SkillRegistry, parse_skill


def write_skill(root: Path, name: str, text: str) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    path = d / "SKILL.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_parses_frontmatter_and_body(tmp_path: Path):
    path = write_skill(
        tmp_path, "退款分析", "---\nname: 退款分析\ndescription: 分析退款\n---\n正文内容"
    )
    skill = parse_skill(path)
    assert skill.name == "退款分析"
    assert skill.body == "正文内容"


def test_rejects_missing_frontmatter(tmp_path: Path):
    path = write_skill(tmp_path, "x", "没有 frontmatter")
    with pytest.raises(SkillError, match="缺少 frontmatter"):
        parse_skill(path)


def test_rejects_empty_description(tmp_path: Path):
    # description 是模型判断要不要加载这个 skill 的唯一依据，空的等于永不生效。
    path = write_skill(tmp_path, "x", "---\nname: x\ndescription: ''\n---\n正文")
    with pytest.raises(SkillError, match="description"):
        parse_skill(path)


def test_rejects_overlong_description(tmp_path: Path):
    # description 常驻 system prompt，每轮都要重发，不能让它膨胀。
    path = write_skill(tmp_path, "x", f"---\nname: x\ndescription: {'长' * 300}\n---\n正文")
    with pytest.raises(SkillError, match="超过"):
        parse_skill(path, max_description_chars=200)


def test_registry_reports_errors_without_failing_others(tmp_path: Path):
    # 业务同学写错 skill 是常态，不能因此让整个平台起不来。
    write_skill(tmp_path, "好的", "---\nname: 好的\ndescription: 正常\n---\n正文")
    write_skill(tmp_path, "坏的", "没有 frontmatter")
    registry = SkillRegistry([str(tmp_path)])
    count, errors = registry.reload()
    assert count == 1
    assert len(errors) == 1
    assert registry.get("好的") is not None


def test_registry_detects_name_conflict(tmp_path: Path):
    write_skill(tmp_path / "a", "重名", "---\nname: 重名\ndescription: 一号\n---\n正文")
    write_skill(tmp_path / "b", "重名", "---\nname: 重名\ndescription: 二号\n---\n正文")
    registry = SkillRegistry([str(tmp_path)])
    count, errors = registry.reload()
    assert count == 1
    assert any("名字冲突" in e for e in errors)


def test_catalog_contains_only_name_and_description(tmp_path: Path):
    """渐进式加载的核心断言：正文绝不能进 system prompt。"""
    write_skill(
        tmp_path,
        "指标",
        "---\nname: 指标\ndescription: 指标口径\n---\n这段很长的正文不应该出现在目录里",
    )
    registry = SkillRegistry([str(tmp_path)])
    registry.reload()
    catalog = registry.catalog_for_prompt()
    assert "指标口径" in catalog
    assert "不应该出现在目录里" not in catalog
