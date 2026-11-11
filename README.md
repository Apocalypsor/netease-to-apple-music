# Apple Music Playlist Migrator

将公开的网易云音乐歌单按歌曲名和歌手匹配后写入 Apple Music 库内歌单。项目是纯命令行程序，不包含 GUI、Selenium 或网易云登录状态。

匹配器会同时比较原文、中文拼音和日文罗马音；带歌手的搜索没有候选或分数不足时，会自动回退到只按标题搜索。低于安全阈值的候选仍会进入失败列表，避免把错误歌曲加入目标歌单。

## 初始化

```bash
uv sync
cp config.example.toml config.toml
```

编辑 `config.toml`：

- `source.playlist_id`：网易云歌单数字 ID。
- `apple_music.storefront`：Apple Music 地区代码，例如 `us` 或 `cn`。
- `apple_music.playlist_id`：目标库歌单 ID，例如 `p.xxxxx`。
- `apple_music.transport`：使用 `web` 或 `public`。
- `public` 模式只填写 `[apple_music.public]` 下的 `developer_token` 和 `user_token`。
- `web` 模式只填写 `[apple_music.web]` 下的 `authorization`、`media_user_token`、`cookie` 和可选的 `client_version`。

浏览器请求里的 Authorization、Media-User-Token 和 Cookie 都是敏感凭证，只保存在本地 `config.toml`，不要提交或发送到聊天。它们失效后重新从浏览器复制。

## 断点续传

状态保存在 `runtime.state_dir` 下的 SQLite 文件中。每首歌曲匹配成功或失败都会立即提交；每个 Apple Music 写入批次也会立即提交。进程中断后重新运行会跳过已经完成的歌曲，不会重新搜索或重复写入。`remote_dedupe = true` 时，启动写入阶段还会读取目标歌单已有的歌曲 ID 并标记为已完成。

失败歌曲默认保留在状态库中；需要重试时把 `runtime.retry_failures` 改为 `true`。

## 运行

先试跑网易云读取：

```bash
uv run apple-music-migrate --dry-run
```

只迁移前 10 首验证匹配：

```bash
uv run apple-music-migrate --limit 10
```

确认结果后迁移完整歌单：

```bash
uv run apple-music-migrate
```

失败歌曲会写入 `state/failed-*.json`，详细匹配和写入状态保存在 `state/playlist-38240993.sqlite3`。
