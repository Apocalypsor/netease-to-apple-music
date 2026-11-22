# Apple Music Playlist Migrator

将公开的网易云音乐歌单按歌曲名和歌手匹配后写入 Apple Music 库内歌单

## 初始化

```bash
uv sync
cp config.example.toml config.toml
```

编辑 `config.toml`：

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
