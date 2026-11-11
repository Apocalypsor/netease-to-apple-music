from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Song:
    source_id: int
    position: int
    title: str
    artist: str


@dataclass(frozen=True)
class AppleTrack:
    catalog_id: str
    title: str
    artist: str
    album: str
    score: float


@dataclass(frozen=True)
class MatchFailure:
    source_id: int
    title: str
    artist: str
    reason: str
