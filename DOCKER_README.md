# Docker 部署说明

## 概述

本项目支持通过 Docker 进行部署，包含两个主要服务：

1. **tg-bot-host**: 主要的 Telegram 机器人服务
2. **tg-verify-server**: 用于 Cloudflare Turnstile 验证的 Web 服务

## 文件说明

- `Dockerfile`: Docker 镜像构建文件
- `docker-compose.yml`: Docker Compose 编排文件
- `docker-entrypoint.sh`: Docker 容器入口点脚本
- `.dockerignore`: Docker 构建忽略文件
- `.env.example`: 环境变量示例文件

## 部署步骤

### 1. 复制并配置环境变量文件

```bash
cp .env.example .env
```

编辑 `.env` 文件，填写必要的配置信息：
- `MANAGER_TOKEN`: 你的 Telegram 机器人 Token
- `ADMIN_CHANNEL`: 管理员频道/群组 ID

### 2. 构建并启动服务

```bash
# 使用预构建镜像启动
docker-compose up -d

# 或者本地构建镜像后启动
# docker-compose up -d --build
```

### 3. 查看服务状态

```bash
# 查看运行中的容器
docker-compose ps

# 查看日志
docker-compose logs -f
```

## 服务说明

### tg-bot-host (主服务)

- 容器名称: `tg_multi_bot`
- 重启策略: `unless-stopped`
- 数据卷: `./data:/app/data` (持久化存储数据库)
- 健康检查: 检查数据库文件是否存在

### tg-verify-server (验证服务)

- 容器名称: `tg_verify_server`
- 端口映射: `80:5000` (将容器的5000端口映射到主机的80端口)
- 数据卷: `./data:/app/data` (与主服务共享数据库)
- 健康检查: 检查数据库文件是否存在
- 命令: `["verify"]` (指定运行验证服务)

## 环境变量配置

### 必需配置

- `MANAGER_TOKEN`: 管理机器人 Token
- `ADMIN_CHANNEL`: 管理员频道/群组 ID

### 可选配置

- `GH_USERNAME`: GitHub 用户名（用于自动备份）
- `GH_REPO`: GitHub 仓库名（用于自动备份）
- `GH_TOKEN`: GitHub Token（用于自动备份）
- `CF_TURNSTILE_SITE_KEY`: Cloudflare Turnstile Site Key
- `CF_TURNSTILE_SECRET_KEY`: Cloudflare Turnstile Secret Key

## 数据持久化

数据库文件存储在 `./data` 目录中，该目录被挂载到两个容器中，确保数据在容器重启后仍然存在。

## 常用命令

```bash
# 启动服务
docker-compose up -d

# 停止服务
docker-compose down

# 重启服务
docker-compose restart

# 查看日志
docker-compose logs -f

# 进入容器
docker-compose exec tg-bot-host /bin/bash
```

## 注意事项

1. 验证服务器需要绑定 80 端口，请确保该端口未被其他服务占用
2. 如果修改了代码，需要重新构建镜像: `docker-compose build`
3. 数据库文件位于 `./data/bot_data.db`，请注意备份