from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from functools import lru_cache

import pykakasi
from pypinyin import lazy_pinyin


_BRACKET_RE = re.compile(r"\([^)]*\)|（[^）]*）|\[[^]]*\]|【[^】]*】")
_NON_TEXT_RE = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)
_KAKASI = pykakasi.kakasi()


def clean_title(title: str) -> str:
    return _BRACKET_RE.sub(" ", title).strip()


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return _NON_TEXT_RE.sub(" ", value).strip()


@lru_cache(maxsize=8192)
def variants(value: str) -> frozenset[str]:
    """Return comparable forms for Unicode titles and artist names."""
    forms = {normalize(value)}
    forms.add(normalize(" ".join(lazy_pinyin(value))))
    forms.add(normalize(" ".join(item["hepburn"] for item in _KAKASI.convert(value))))
    return frozenset(form for form in forms if form)


def _best_similarity(source: str, target: str) -> float:
    source_forms = variants(source)
    target_forms = variants(target)
    if not source_forms or not target_forms:
        return 0.0
    return max(
        SequenceMatcher(None, source_form, target_form).ratio()
        for source_form in source_forms
        for target_form in target_forms
    )


def score(source_title: str, source_artist: str, target_title: str, target_artist: str) -> float:
    title_values = (source_title, clean_title(source_title))
    title_score = max(_best_similarity(source, target_title) for source in title_values)
    artist_score = _best_similarity(source_artist, target_artist)
    if variants(clean_title(source_title)) & variants(clean_title(target_title)):
        title_score = 1.0
    if variants(source_artist) & variants(target_artist):
        artist_score = 1.0
    return 0.72 * title_score + 0.28 * artist_score
