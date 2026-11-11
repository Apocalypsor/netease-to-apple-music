from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """Raised when the local TOML configuration is invalid."""


@dataclass(frozen=True)
class PublicAuth:
    developer_token: str
    user_token: str


@dataclass(frozen=True)
class WebAuth:
    authorization: str
    media_user_token: str
    cookie: str
    client_version: str


@dataclass(frozen=True)
class RuntimeConfig:
    request_delay: float = 1.2
    batch_size: int = 100
    search_limit: int = 5
    min_match_score: float = 0.58
    max_retries: int = 4
    timeout: float = 30.0
    state_dir: Path = Path("state")
    retry_failures: bool = False
    remote_dedupe: bool = True


@dataclass(frozen=True)
class Settings:
    source_playlist_id: int
    storefront: str
    target_playlist_id: str
    transport: str
    public: PublicAuth
    web: WebAuth
    runtime: RuntimeConfig

    @classmethod
    def load(cls, path: Path, *, require_credentials: bool, require_target: bool) -> "Settings":
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ConfigError(f"找不到配置文件：{path}") from exc
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"TOML 格式错误：{exc}") from exc

        source = _table(raw, "source")
        apple = _table(raw, "apple_music")
        public_raw = _table(apple, "public")
        web_raw = _table(apple, "web")
        runtime_raw = _table(raw, "runtime")

        playlist_id = _positive_int(source.get("playlist_id"), "source.playlist_id")
        storefront = _required_str(apple.get("storefront"), "apple_music.storefront").lower()
        if len(storefront) != 2 or not storefront.isalpha():
            raise ConfigError("apple_music.storefront 必须是两位地区代码，例如 us 或 cn")

        target = str(apple.get("playlist_id", "")).strip()
        if require_target and not target:
            raise ConfigError("请填写 apple_music.playlist_id")

        transport = str(apple.get("transport", "web")).strip().lower()
        if transport not in {"public", "web"}:
            raise ConfigError("apple_music.transport 必须是 public 或 web")

        public = PublicAuth(
            developer_token=str(public_raw.get("developer_token", "")).strip(),
            user_token=str(public_raw.get("user_token", "")).strip(),
        )
        web = WebAuth(
            authorization=str(web_raw.get("authorization", "")).strip(),
            media_user_token=str(web_raw.get("media_user_token", "")).strip(),
            cookie=str(web_raw.get("cookie", "")).strip(),
            client_version=str(web_raw.get("client_version", "2644.9.0-external")).strip(),
        )
        if require_credentials and transport == "public" and (
            not public.developer_token or not public.user_token
        ):
            raise ConfigError("public 认证需要 apple_music.public 下的两个令牌")
        if require_credentials and transport == "web" and (
            not web.authorization or not web.media_user_token or not web.cookie
        ):
            raise ConfigError(
                "web 认证需要 apple_music.web.authorization、media_user_token 和 cookie"
            )

        runtime = RuntimeConfig(
            request_delay=_number(runtime_raw.get("request_delay", 1.2), "runtime.request_delay"),
            batch_size=_bounded_int(runtime_raw.get("batch_size", 100), "runtime.batch_size", 1, 100),
            search_limit=_bounded_int(runtime_raw.get("search_limit", 5), "runtime.search_limit", 1, 25),
            min_match_score=_number(runtime_raw.get("min_match_score", 0.58), "runtime.min_match_score"),
            max_retries=_bounded_int(runtime_raw.get("max_retries", 4), "runtime.max_retries", 0, 8),
            timeout=_number(runtime_raw.get("timeout", 30.0), "runtime.timeout"),
            state_dir=Path(str(runtime_raw.get("state_dir", "state"))),
            retry_failures=bool(runtime_raw.get("retry_failures", False)),
            remote_dedupe=bool(runtime_raw.get("remote_dedupe", True)),
        )
        if runtime.request_delay < 0 or runtime.timeout <= 0:
            raise ConfigError("runtime.request_delay 和 runtime.timeout 必须为正数或零")
        if not 0 <= runtime.min_match_score <= 1:
            raise ConfigError("runtime.min_match_score 必须在 0 到 1 之间")

        return cls(
            source_playlist_id=playlist_id,
            storefront=storefront,
            target_playlist_id=target,
            transport=transport,
            public=public,
            web=web,
            runtime=runtime,
        )


def _table(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] 必须是 TOML 表")
    return value


def _required_str(value: Any, name: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ConfigError(f"请填写 {name}")
    return result


def _positive_int(value: Any, name: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} 必须是正整数") from exc
    if result <= 0:
        raise ConfigError(f"{name} 必须是正整数")
    return result


def _bounded_int(value: Any, name: str, low: int, high: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} 必须是整数") from exc
    if not low <= result <= high:
        raise ConfigError(f"{name} 必须在 {low} 到 {high} 之间")
    return result


def _number(value: Any, name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} 必须是数字") from exc
