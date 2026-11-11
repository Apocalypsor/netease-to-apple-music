from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import ConfigError, Settings
from .http import RemoteError
from .migration import Migrator
from .state import StateStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="可断点续传的网易云歌单到 Apple Music 迁移工具")
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    parser.add_argument("--limit", type=int, help="只处理前 N 首，用于试跑")
    parser.add_argument("--retry-file", type=Path, help="只重试旧失败列表中的 source_id")
    parser.add_argument("--dry-run", action="store_true", help="只读取网易云歌单，不访问 Apple Music")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.limit is not None and args.limit <= 0:
        print("错误：--limit 必须是正整数", file=sys.stderr)
        return 2
    try:
        settings = Settings.load(
            args.config,
            require_credentials=not args.dry_run,
            require_target=not args.dry_run,
        )
        state_path = settings.runtime.state_dir / f"playlist-{settings.source_playlist_id}.sqlite3"
        store = StateStore(state_path)
        try:
            retry_source_ids = _load_retry_ids(args.retry_file) if args.retry_file else None
            return Migrator(settings, store).run(
                limit=args.limit,
                dry_run=args.dry_run,
                retry_source_ids=retry_source_ids,
            )
        finally:
            store.close()
    except (ConfigError, RemoteError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


def _load_retry_ids(path: Path) -> set[int]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise ValueError(f"失败列表无法读取：{path}") from exc
    items = raw.get("failures", raw) if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise ValueError("失败列表必须是 JSON 数组或包含 failures 数组的对象")
    ids = {int(item["source_id"]) for item in items if isinstance(item, dict) and item.get("source_id")}
    if not ids:
        raise ValueError("失败列表中没有 source_id")
    return ids


if __name__ == "__main__":
    raise SystemExit(main())
