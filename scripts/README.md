# 开发与验证脚本

这些脚本不参与运行时，仅用于开发期验证。除 `demo_pipeline.py` 外都需要先启动服务：

```bash
python -m app.main
```

| 脚本 | 用途 |
|---|---|
| `smoke_test.py` | 对运行中的实例逐个调用全部 67 项接口用例（含鉴权拦截、自定义站点、追新雷达、插件启停与动作），全部通过才退出 0 |
| `ui_check.py` | 用 Playwright 真实浏览器逐页点检 9 个前端页面（含追新雷达、站点模板/发现弹窗），捕获任何 JS 报错与失败请求，截图存 `data/ui_shots/` |
| `demo_pipeline.py` | 用真实磁盘文件演示「解析 → 硬链入库 → 规范命名 → 缺集收敛」全链路，无需启动服务 |
| `verify_docs.py` | 校验 README / scripts/README 里的事实性声明（Provider 数量、端点数、表数、页面数、JSON 示例合法性等）与代码实际一致 |
| `live_check.py` | **联网**验证真实站点闭环：启用 mukaku 预设 → 真实搜索（磁力+网盘）→ 建订阅 → 追新雷达 dry-run 匹配 → 清理 |

`ui_check.py` 需要额外依赖：

```bash
pip install playwright
python -m playwright install chromium
```
