from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .apple_music import AppleMusicClient
from .config import Settings
from .http import RateLimitError, RemoteError
from .models import AppleTrack, Song
from .netease import fetch_playlist
from .state import StateStore


class Migrator:
    def __init__(self, settings: Settings, store: StateStore) -> None:
        self.settings = settings
        self.store = store

    def run(
        self,
        *,
        limit: int | None = None,
        dry_run: bool = False,
        retry_source_ids: set[int] | None = None,
    ) -> int:
        source_client = self._source_client()
        songs = fetch_playlist(source_client, self.settings.source_playlist_id)
        self.store.ensure_identity(
            self.settings.source_playlist_id, self.settings.target_playlist_id
        )
        self.store.sync_songs(songs)
        self.store.recover_inflight()
        print(f"歌单读取完成：{len(songs)} 首；状态库：{self.store.path}")

        if dry_run:
            for song in songs[:10]:
                print(f"  - {song.title} / {song.artist}")
            return 0

        apple = AppleMusicClient(self.settings)
        try:
            self._search_pending(apple, limit=limit, source_ids=retry_source_ids)
            self._add_pending(apple, limit=limit, source_ids=retry_source_ids)
        except RateLimitError as exc:
            print(f"Apple Music 限流仍未解除，已安全停止：{exc}")
            print("已完成的匹配和写入都保存在状态库，稍后重新运行会从断点继续。")
            return 3

        counts = self.store.counts()
        print(
            f"完成：总计 {counts['songs']}，已匹配 {counts['matched']}，"
            f"已写入 {counts['added']}，失败 {counts['failed']}，待处理 {counts['pending']}"
        )
        failure_file = self._export_failures_if_needed()
        if failure_file:
            print(f"失败记录：{failure_file}")
        return 1 if counts["failed"] else 0

    def _search_pending(
        self,
        apple: AppleMusicClient,
        *,
        limit: int | None,
        source_ids: set[int] | None,
    ) -> None:
        pending = self.store.pending_matches(
            self.settings.runtime.retry_failures,
            limit=limit,
            source_ids=source_ids,
        )
        if not pending:
            print("搜索阶段：没有需要重新搜索的歌曲")
            return
        print(f"搜索阶段：待处理 {len(pending)} 首，结果会逐首写入状态库")
        for index, song in enumerate(pending, start=1):
            try:
                match = apple.search(song)
                self.store.save_match(song, match, None)
                print(
                    f"搜索 [{index}/{len(pending)}]：{song.title} / {song.artist}"
                    f" -> {match.title} / {match.artist} ({match.score:.2f})"
                )
            except RateLimitError:
                raise
            except Exception as exc:  # noqa: BLE001 - one bad song must not lose the checkpoint
                self.store.save_match(song, None, str(exc))
                print(f"搜索失败 [{index}/{len(pending)}]：{song.title} / {song.artist}：{exc}")

    def _add_pending(
        self,
        apple: AppleMusicClient,
        *,
        limit: int | None,
        source_ids: set[int] | None,
    ) -> None:
        if self.settings.runtime.remote_dedupe:
            existing = apple.list_playlist_catalog_ids()
            skipped = self.store.mark_existing_catalog_ids(existing)
            if skipped:
                print(f"远端去重：发现并跳过 {skipped} 首已在目标歌单中的歌曲")

        pending = self.store.matched_unwritten(limit=limit, source_ids=source_ids)
        if not pending:
            print("写入阶段：没有待写入歌曲")
            return
        print(
            f"写入阶段：{len(pending)} 首，批大小 {self.settings.runtime.batch_size}；"
            "每个批次完成后立即提交状态"
        )
        for batch in _chunks(pending, self.settings.runtime.batch_size):
            self._add_batch(apple, batch)

    def _add_batch(self, apple: AppleMusicClient, batch: list[tuple[int, AppleTrack]]) -> None:
        source_ids = [source_id for source_id, _ in batch]
        tracks = [track for _, track in batch]
        self.store.mark_writing(source_ids)
        try:
            apple.add_tracks(tracks)
        except RateLimitError:
            raise
        except Exception as exc:  # retry individually to isolate one invalid resource
            if len(batch) == 1:
                self.store.mark_failed(source_ids, str(exc))
                print(f"写入失败：{tracks[0].title} / {tracks[0].artist}：{exc}")
                return
            print(f"批量写入失败，改为逐首重试：{exc}")
            for source_id, track in batch:
                self.store.mark_writing([source_id])
                try:
                    apple.add_tracks([track])
                    self.store.mark_added([source_id])
                except RateLimitError:
                    raise
                except Exception as item_exc:  # noqa: BLE001
                    self.store.mark_failed([source_id], str(item_exc))
                    print(f"写入失败：{track.title} / {track.artist}：{item_exc}")
            return
        self.store.mark_added(source_ids)
        print(f"写入成功：{len(source_ids)} 首")

    def _source_client(self):
        from .http import HttpClient

        return HttpClient(
            timeout=self.settings.runtime.timeout,
            max_retries=self.settings.runtime.max_retries,
            user_agent="apple-music-playlist-migrator/0.2",
        )

    def _export_failures_if_needed(self) -> Path | None:
        failures = self.store.failures()
        if not failures:
            return None
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output = self.store.path.parent / f"failed-{timestamp}.json"
        self.store.export_failures(output)
        return output


def _chunks(items: list[tuple[int, AppleTrack]], size: int) -> Iterable[list[tuple[int, AppleTrack]]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]
