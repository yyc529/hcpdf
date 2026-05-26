from __future__ import annotations

import re


LATIN_FALLBACK = '"Arial", "Helvetica", sans-serif'
CJK_FALLBACK = (
    '"Noto Sans CJK SC", "Source Han Sans SC", "Microsoft YaHei", '
    '"PingFang SC", "Hiragino Sans GB", "SimSun", sans-serif'
)
SERIF_FALLBACK = '"Noto Serif CJK SC", "Source Han Serif SC", "SimSun", "Times New Roman", serif'
MONO_FALLBACK = '"Source Code Pro", "Menlo", "Consolas", monospace'

SUBSET_PREFIX_RE = re.compile(r"^[A-Z]{6}\+")
_CJK_FONT_HINT = re.compile(r"(YaHei|SimSun|SimHei|FangSong|KaiTi|PingFang|Heiti|Songti|MingLiU|"
                            r"Source ?Han|Noto ?CJK|MS\s*Gothic|MS\s*Mincho|HiraKaku|HiraMin|"
                            r"NanumGothic|Malgun)", re.IGNORECASE)
_SERIF_HINT = re.compile(r"(Serif|Times|Ming|Songti|Cambria|Georgia|Garamond)", re.IGNORECASE)
_MONO_HINT = re.compile(r"(Mono|Courier|Consolas|Menlo|Code)", re.IGNORECASE)
_BOLD_HINT = re.compile(r"(Bold|Heavy|Black|Semibold|Demibold)", re.IGNORECASE)
_ITALIC_HINT = re.compile(r"(Italic|Oblique)", re.IGNORECASE)

# CJK Unicode ranges (sample): CJK Unified, CJK Extensions A+B, Kangxi, Hiragana/Katakana, Hangul.
_CJK_CHAR_RE = re.compile(
    "["
    "　-〿"
    "぀-ヿ"
    "ㇰ-ㇿ"
    "㐀-䶿"
    "一-鿿"
    "가-힯"
    "豈-﫿"
    "＀-￯"
    "]"
)


def clean_font_name(font_name: str | None) -> str:
    if not font_name:
        return "FallbackSans"
    cleaned = SUBSET_PREFIX_RE.sub("", str(font_name)).strip()
    return cleaned or "FallbackSans"


def css_family_name(font_name: str | None) -> str:
    return clean_font_name(font_name).replace('"', "")


def is_cjk_font_name(font_name: str | None) -> bool:
    return bool(_CJK_FONT_HINT.search(font_name or ""))


def font_is_bold(font_name: str | None, flags: int = 0) -> bool:
    if flags & 16:
        return True
    return bool(_BOLD_HINT.search(font_name or ""))


def font_is_italic(font_name: str | None, flags: int = 0) -> bool:
    if flags & 2:
        return True
    return bool(_ITALIC_HINT.search(font_name or ""))


def has_cjk_text(text: str) -> bool:
    return bool(_CJK_CHAR_RE.search(text or ""))


def _fallback_chain(font_name: str | None, prefer_cjk: bool) -> str:
    name = font_name or ""
    if _MONO_HINT.search(name):
        return MONO_FALLBACK
    if prefer_cjk or is_cjk_font_name(name):
        return CJK_FALLBACK
    if _SERIF_HINT.search(name):
        return SERIF_FALLBACK
    return LATIN_FALLBACK


def font_stack(font_name: str | None, prefer_cjk: bool = False) -> str:
    family = css_family_name(font_name)
    return f'"{family}", {_fallback_chain(font_name, prefer_cjk)}'
