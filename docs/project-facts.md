# 项目事实（自动生成）

运行 `python scripts/project_facts.py --write` 更新；CI 使用 `--check` 校验。

| 项目 | 当前值 |
|---|---:|
| 版本 | 1.29.1 |
| API 端点 | 184 |
| router | 24 |
| 数据库表 | 20 |
| Provider | 41 |
| 可访问页面 | 22 |
| 一级导航 | 7 |
| 内置调度任务 | 14 |
| 默认站点 | 22 |
| 配置分组 | 17 |
| 兼容配置键 | 63 |
| 测试模块 | 68 |
| 规则组模板 | 4 |
| 发现榜分类 | 7 |

调度周期的兼容配置键仍可通过 API 访问，实际只写调度配置；设置页引导至定时任务。

| Provider 类别 | 数量 | 名称 |
|---|---:|---|
| downloader | 5 | `aria2`, `qbittorrent`, `transmission`, `xunlei`, `ytdlp` |
| indexer | 18 | `animetosho`, `api_generic`, `bilibili`, `bitsearch`, `bt4g`, `html_generic`, `knaben`, `maccms`, `mukaku`, `nyaa`, `rss`, `therarbg`, `torrents_csv`, `torznab`, `tpb`, `wp_film`, `youtube`, `yyets` |
| mediaserver | 3 | `emby`, `jellyfin`, `plex` |
| notifier | 4 | `bark`, `telegram`, `webhook`, `wecom` |
| pan | 3 | `kkso`, `pan_generic`, `pansou` |
| panstorage | 8 | `alipan`, `alist`, `baidu`, `baidu_openapi`, `local_dir`, `pan115`, `quark`, `webdav` |
