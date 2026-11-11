from __future__ import annotations

import json
from typing import Any, Iterable

from .http import HttpClient, RemoteError
from .models import Song


_PLAYLIST_URL = "https://music.163.com/api/v6/playlist/detail"
_SONG_DETAIL_URL = "https://music.163.com/api/v3/song/detail"


def fetch_playlist(client: HttpClient, playlist_id: int) -> list[Song]:
    data = client.get_json(_PLAYLIST_URL, params={"id": playlist_id})
    if data.get("code") != 200 or not isinstance(data.get("playlist"), dict):
        raise RemoteError("网易云歌单读取失败，请确认歌单公开且 ID 正确")

    playlist = data["playlist"]
    track_ids = [
        int(item["id"])
        for item in playlist.get("trackIds", [])
        if isinstance(item, dict) and item.get("id")
    ]
    known: dict[int, dict[str, Any]] = {
        int(item["id"]): item
        for item in playlist.get("tracks", [])
        if isinstance(item, dict) and item.get("id")
    }
    if not track_ids:
        track_ids = list(known)

    for chunk in _chunks(track_ids, 100):
        payload = json.dumps([{"id": song_id} for song_id in chunk], separators=(",", ":"))
        details = client.get_json(_SONG_DETAIL_URL, params={"c": payload})
        for item in details.get("songs", []):
            if isinstance(item, dict) and item.get("id"):
                known[int(item["id"])] = item

    songs: list[Song] = []
    seen: set[tuple[str, str]] = set()
    for source_id in track_ids:
        item = known.get(source_id)
        if not item or not item.get("name"):
            continue
        title = str(item["name"]).strip()
        artists = item.get("ar") or item.get("artists") or []
        artist = " & ".join(
            str(artist_item.get("name", "")).strip()
            for artist_item in artists
            if isinstance(artist_item, dict) and artist_item.get("name")
        ) or "未知歌手"
        key = (title.casefold(), artist.casefold())
        if key in seen:
            continue
        songs.append(Song(source_id=source_id, position=len(songs), title=title, artist=artist))
        seen.add(key)

    if not songs:
        raise RemoteError("网易云歌单中没有可迁移的歌曲")
    return songs


def _chunks(items: list[Any], size: int) -> Iterable[list[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]
