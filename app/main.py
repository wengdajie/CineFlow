"""CineFlow 应用入口。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.core.config import settings
from app.core.exceptions import CineFlowError
from app.core.logger import get_logger, setup_logging
from app.core.version import API_PREFIX, APP_TITLE, APP_VERSION
from app.db.init_db import init_db
from app.plugins.manager import plugin_manager
from app.providers.registry import load_builtin_providers
from app.services import config_store
from app.services.scheduler import scheduler_service

logger = get_logger(__name__)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动与关闭流程。"""
    setup_logging()
    logger.info("=" * 60)
    logger.info("%s v%s 正在启动…", APP_TITLE, APP_VERSION)

    init_db()
    # 顺序很重要：先把数据库里的运行期配置覆盖套回 settings 单例，
    # 再启动调度器——否则调度器会按 .env 里的旧周期建触发器
    config_store.apply_overrides()
    load_builtin_providers()
    await plugin_manager.load_enabled()
    scheduler_service.start()

    logger.info("服务已就绪：http://%s:%s", settings.HOST, settings.PORT)
    logger.info("=" * 60)
    try:
        yield
    finally:
        scheduler_service.shutdown()
        logger.info("%s 已停止", APP_TITLE)


app = FastAPI(
    title=APP_TITLE,
    version=APP_VERSION,
    description=(
        "面向 NAS 的自动化观影追剧平台：聚合 BT 站点与网盘搜索，"
        "自动追新、下载、刮削命名、入库并通知。"
    ),
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=API_PREFIX)


@app.exception_handler(CineFlowError)
async def cineflow_error_handler(_: Request, exc: CineFlowError) -> JSONResponse:
    """统一业务异常响应。"""
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "message": exc.message, "detail": exc.detail},
    )


@app.get("/api/health", include_in_schema=False)
async def health() -> dict:
    """健康检查（供 Docker/反代使用）。"""
    return {
        "status": "ok",
        "version": APP_VERSION,
        "scheduler": scheduler_service.running,
    }


class _NoCacheStatic(StaticFiles):
    """静态资源强制走「协商缓存」，不允许浏览器凭启发式规则直接用本地副本。

    **为什么必须显式设**：Starlette 的 StaticFiles 只给 ETag 与 Last-Modified，
    **不给 Cache-Control**。而 RFC 7234 允许浏览器在没有 Cache-Control 时
    自行推算新鲜期（常见实现是「距上次修改时间的 10%」）——
    也就是说容器升级后，浏览器可能**根本不来问服务器**就继续用旧的
    ``app.js`` / ``style.css``，用户看到的还是上个版本的界面，
    只能靠手动强刷（Ctrl+F5）绕过。这个坑很隐蔽：后端 ``/api/health``
    已经是新版本号，界面却是旧的，容易误判成「镜像没更新成功」。

    ``no-cache`` 的语义不是"不缓存"，而是"每次都必须回源校验"。
    配合已有的 ETag，未变更时返回 304（几百字节），代价极小，
    但能保证**升级后必然拿到新代码**。
    """

    def file_response(self, *args: object, **kwargs: object) -> Response:
        response = super().file_response(*args, **kwargs)  # type: ignore[arg-type]
        response.headers["Cache-Control"] = "no-cache"
        return response


if WEB_DIR.exists():
    assets_dir = WEB_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", _NoCacheStatic(directory=assets_dir), name="assets")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        """Web 控制台。

        入口文档同样禁止启发式缓存：它引用的是不带版本指纹的
        ``/assets/app.js``，一旦 index.html 被缓存住，后续所有资源
        都会跟着停留在旧版本。
        """
        return FileResponse(
            WEB_DIR / "index.html",
            headers={"Cache-Control": "no-cache"},
        )


def run() -> None:
    """以 uvicorn 启动（供 ``python -m app.main`` 使用）。"""
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        log_config=None,
    )


if __name__ == "__main__":
    run()
