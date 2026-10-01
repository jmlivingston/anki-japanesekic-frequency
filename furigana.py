"""Shared helper for generating Anki-style bracket furigana ("漢字[かんじ]")
from plain Japanese text, using fugashi/unidic token readings."""
from __future__ import annotations

from fugashi import Tagger

_KATA_TO_HIRA = str.maketrans({chr(c): chr(c - 0x60) for c in range(0x30A1, 0x30F7)})


def _is_kanji(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff"


def _token_furigana(surface: str, kana: str) -> str:
    """Bracket-annotate only the kanji portion of a token, trimming any
    okurigana (kana) that the surface and reading share at the edges, e.g.
    ("食べる", "タベル") -> "食[た]べる", not "食べる[たべる]"."""
    if not any(_is_kanji(c) for c in surface):
        return surface
    reading = kana.translate(_KATA_TO_HIRA) if kana else surface

    i = 0
    while (
        i < len(surface)
        and i < len(reading)
        and surface[-1 - i] == reading[-1 - i]
        and not _is_kanji(surface[-1 - i])
    ):
        i += 1
    suffix = surface[len(surface) - i :] if i else ""
    core_surface = surface[: len(surface) - i] if i else surface
    core_reading = reading[: len(reading) - i] if i else reading

    j = 0
    while (
        j < len(core_surface)
        and j < len(core_reading)
        and core_surface[j] == core_reading[j]
        and not _is_kanji(core_surface[j])
    ):
        j += 1
    prefix = core_surface[:j]
    core_surface = core_surface[j:]
    core_reading = core_reading[j:]

    if not core_surface:
        return surface
    return f"{prefix}{core_surface}[{core_reading}]{suffix}"


def sentence_to_furigana(text: str, tagger: Tagger) -> str:
    """Convert plain Japanese text into Anki bracket-furigana notation."""
    if not text:
        return text
    return "".join(_token_furigana(w.surface, w.feature.kana) for w in tagger(text))
