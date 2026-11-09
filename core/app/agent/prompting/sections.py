"""Deterministic trusted prompt sections. / 确定性可信提示词分节。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PromptSection:
    """A named static contribution. / 按名称和顺序定位的静态内容。"""

    name: str
    order: int
    text: str


def assemble_sections(sections: list[PromptSection]) -> str:
    """Resolve order/name deterministically; duplicate names are configuration errors.
    / 按 order/name 确定性组装；重复名称是配置错误。"""
    if len({s.name for s in sections}) != len(sections):
        raise ValueError("Prompt section names must be unique")
    return "\n\n".join(
        s.text.strip() for s in sorted(sections, key=lambda s: (s.order, s.name)) if s.text.strip()
    )
