#!/bin/bash
set -e

# 根据传入的参数决定运行哪个服务
if [ "$1" = "host" ]; then
    echo "🚀 启动 Telegram Bot Host 服务..."
    exec python host_bot.py
elif [ "$1" = "verify" ]; then
    echo "🚀 启动 Telegram Verify Server 服务..."
    exec python verify_server.py
else
    # 默认运行 host 服务
    echo "🚀 启动默认服务 (Telegram Bot Host)..."
    exec python host_bot.py
fi