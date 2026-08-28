#!/bin/sh
# CineFlow 容器入口：处理 PUID/PGID、首次生成配置、目录权限
set -e

PUID=${PUID:-0}
PGID=${PGID:-0}

echo "----------------------------------------"
echo " CineFlow 启动中"
echo " PUID=${PUID}  PGID=${PGID}  TZ=${TZ}"
echo "----------------------------------------"

# 首次运行：从示例生成配置文件（不覆盖已有配置）
if [ ! -f /app/config/config.yaml ] && [ -f /app/config/config.yaml.example ]; then
    cp /app/config/config.yaml.example /app/config/config.yaml
    echo "已生成默认配置：/app/config/config.yaml"
fi

# 确保运行期目录存在
mkdir -p /app/data/logs /app/data/cache /app/config /app/plugins /downloads /library /strm

# 以非 root 身份运行（群晖/威联通建议设置 PUID/PGID 与共享文件夹归属一致）
if [ "${PUID}" != "0" ]; then
    chown -R "${PUID}:${PGID}" /app/data /app/config /app/plugins 2>/dev/null || true
    if command -v setpriv >/dev/null 2>&1; then
        exec setpriv --reuid "${PUID}" --regid "${PGID}" --clear-groups "$@"
    fi
    echo "警告：找不到 setpriv，将以 root 运行"
fi

exec "$@"
