# ============================================================
# CineFlow —— NAS 自动化观影追剧平台
# 多阶段构建，最终镜像仅含运行期依赖
# ============================================================
FROM python:3.12-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt


FROM python:3.12-slim

LABEL org.opencontainers.image.title="CineFlow" \
      org.opencontainers.image.description="NAS 自动化观影追剧平台：BT 站点 + 网盘搜索、自动追新、刮削入库" \
      org.opencontainers.image.source="https://github.com/your-name/cineflow" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    TZ=Asia/Shanghai \
    CF_DATA_DIR=/app/data \
    CF_DOWNLOAD_DIR=/downloads \
    CF_LIBRARY_DIR=/library \
    CF_STRM_DIR=/strm \
    CF_PLUGIN_DIR=/app/plugins \
    CF_CONFIG_FILE=/app/config/config.yaml

# tzdata 用于时区，curl 用于健康检查
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata curl util-linux \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY app/ ./app/
COPY web/ ./web/
COPY plugins/ ./plugins/
COPY config/config.yaml.example ./config/config.yaml.example
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh \
    && mkdir -p /app/data /app/config /downloads /library /strm

VOLUME ["/app/data", "/app/config", "/app/plugins", "/downloads", "/library", "/strm"]

EXPOSE 8611

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8611/api/health || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "app.main"]
