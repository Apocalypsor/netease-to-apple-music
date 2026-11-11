from __future__ import annotations

from typing import Any

from .config import Settings
from .http import HttpClient
from .matching import clean_title, score
from .models import AppleTrack, Song


class AppleMusicClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        web = settings.transport == "web"
        user_agent = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36"
            if web
            else "apple-music-playlist-migrator/0.2"
        )
        self.http = HttpClient(
            timeout=settings.runtime.timeout,
            max_retries=settings.runtime.max_retries,
            user_agent=user_agent,
        )
        if web:
            authorization = settings.web.authorization
            if not authorization.lower().startswith("bearer "):
                authorization = f"Bearer {authorization}"
            self.headers = {
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.9",
                "Authorization": authorization,
                "Content-Type": "application/json",
                "Cookie": settings.web.cookie,
                "Media-User-Token": settings.web.media_user_token,
                "Origin": "https://music.apple.com",
                "Referer": "https://music.apple.com/",
                "X-Apple-Client-Version": settings.web.client_version,
            }
            host = "https://amp-api-edge.music.apple.com"
        else:
            self.headers = {
                "Authorization": f"Bearer {settings.public.developer_token}",
                "Music-User-Token": settings.public.user_token,
                "Origin": "https://music.apple.com",
                "Content-Type": "application/json",
            }
            host = "https://api.music.apple.com"
        self.search_url = f"{host}/v1/catalog/{settings.storefront}/search"
        self.playlist_tracks_url = (
            f"{host}/v1/me/library/playlists/{settings.target_playlist_id}/tracks"
        )

    def search(self, song: Song) -> AppleTrack:
        title = clean_title(song.title)
        queries = [f"{title} {song.artist}".strip(), title]
        candidates_by_id: dict[str, dict[str, Any]] = {}
        best_score = 0.0
        for query_index, query in enumerate(queries):
            params: dict[str, Any] = {
                "term": query,
                "types": "songs",
                "limit": self.settings.runtime.search_limit,
            }
            if self.settings.transport == "web":
                params.update({"platform": "web", "l": "en-US", "with": "naturalLanguage"})
            data = self.http.get_json(self.search_url, params=params, headers=self.headers)
            candidates = data.get("results", {}).get("songs", {}).get("data", [])
            if isinstance(candidates, list):
                for item in candidates:
                    if isinstance(item, dict) and item.get("id"):
                        candidates_by_id[str(item["id"])] = item
            if query_index == 0:
                best_score = self._best_candidate_score(song, candidates)
            if best_score >= self.settings.runtime.min_match_score:
                break

        ranked: list[AppleTrack] = []
        for item in candidates_by_id.values():
            if not isinstance(item, dict):
                continue
            attributes = item.get("attributes", {})
            if not isinstance(attributes, dict):
                continue
            catalog_id = str(item.get("id", "")).strip()
            title = str(attributes.get("name", "")).strip()
            artist = str(attributes.get("artistName", "")).strip()
            if not catalog_id or not title:
                continue
            ranked.append(
                AppleTrack(
                    catalog_id=catalog_id,
                    title=title,
                    artist=artist,
                    album=str(attributes.get("albumName", "")).strip(),
                    score=score(song.title, song.artist, title, artist),
                )
            )
        if not ranked:
            raise RuntimeError("Apple Music 没有返回歌曲候选")
        best = max(ranked, key=lambda item: item.score)
        if best.score < self.settings.runtime.min_match_score:
            raise RuntimeError(
                f"最佳候选匹配分数过低 ({best.score:.2f})：{best.title} / {best.artist}"
            )
        return best

    def _best_candidate_score(self, song: Song, candidates: Any) -> float:
        if not isinstance(candidates, list):
            return 0.0
        best = 0.0
        for item in candidates:
            if not isinstance(item, dict):
                continue
            attributes = item.get("attributes", {})
            if not isinstance(attributes, dict):
                continue
            best = max(
                best,
                score(
                    song.title,
                    song.artist,
                    str(attributes.get("name", "")),
                    str(attributes.get("artistName", "")),
                ),
            )
        return best

    def list_playlist_catalog_ids(self) -> set[str]:
        ids: set[str] = set()
        offset = 0
        while True:
            data = self.http.get_json(
                self.playlist_tracks_url,
                params={"limit": 100, "offset": offset},
                headers=self.headers,
            )
            items = data.get("data", [])
            if not isinstance(items, list):
                break
            for item in items:
                if not isinstance(item, dict):
                    continue
                if item.get("id"):
                    ids.add(str(item["id"]))
                catalog = (
                    item.get("relationships", {})
                    .get("catalog", {})
                    .get("data", [])
                )
                if isinstance(catalog, list):
                    ids.update(str(resource["id"]) for resource in catalog if resource.get("id"))
            if len(items) < 100:
                break
            offset += len(items)
        return ids

    def add_tracks(self, tracks: list[AppleTrack]) -> None:
        if not tracks:
            return
        payload = {"data": [{"id": track.catalog_id, "type": "songs"} for track in tracks]}
        self.http.post_json(self.playlist_tracks_url, payload, headers=self.headers)
