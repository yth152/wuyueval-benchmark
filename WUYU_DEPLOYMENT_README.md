# Wuyu AI 审核部署包

本目录新增的 `wuyu-ai-platform/` 是经过安全审计和脱敏的正式版部署包，另附 `wuyu-official-deployment.tar.gz` 归档。

仓库新增内容不包含生产数据库、用户资料、密钥、`.env` 文件、虚拟环境或依赖缓存。

## 下载

在 GitHub 页面点击 **Code → Download ZIP**；也可以单独下载 `wuyu-official-deployment.tar.gz`。

## 部署概要

1. 进入 `wuyu-ai-platform/`，使用 Python 3.11 或更高版本创建虚拟环境。
2. 安装 `official/requirements.txt`，在项目根目录运行 `npm ci`；如需重新构建正式前端，运行 `node ../node_modules/vite/bin/vite.js build --config vite.config.mjs`（在 `official/` 目录执行）。
3. 创建独立数据目录，并按 `official/deploy/platform.env.example` 配置真实域名、模型服务和密钥；不要把密钥提交到 Git。
4. 使用 `official/deploy/wuyu.service` 和 `official/deploy/nginx.conf.example` 部署。公网环境必须使用 HTTPS、精确的 `WUYU_ALLOWED_ORIGINS`、`WUYU_SECURE_COOKIE=1`，服务只监听本机回环地址。
5. 生产环境保持单进程、单 worker，并先运行 `python manage.py create-admin` 创建管理员。

完整说明见 `wuyu-ai-platform/official/README.md`，审计记录见 `wuyu-ai-platform/SECURITY_AUDIT_20261009.md`。

归档 SHA-256：`b62c207e5f21ab900d85e72ac5829d5252d93c5b14ac16359e041a22382bf899`
