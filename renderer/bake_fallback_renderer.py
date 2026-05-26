from __future__ import annotations


def render_baked_placeholder(reason: str) -> str:
    return f"<!-- baked fallback not used in MVP: {reason} -->"
