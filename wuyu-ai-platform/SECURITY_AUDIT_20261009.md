# 无隅 AI 平台审计记录（2026-10-09）

本次审计针对 `official/app_main.py` 正式入口、`model-chat` 解析与 Agent 核心、React 正式构建，以及部署模板进行。上传包中的数据库、密钥、用户资料和历史运行目录只用于私密验证，没有写入本报告。

## 已修复

- **正式入口无法启动（高）**：仓库根目录的旧 `server` 包会遮蔽 `model-chat/server.py`，按交接文档启动时出现 `ImportError: cannot import name 'ChatRequest'`。正式入口现在优先插入 `model-chat` 路径，并已用 uvicorn 单 worker 实际启动验证。
- **旧缓存页面请求图标返回 500（中）**：`/favicon.svg` 指向不存在的文件；现在 `/favicon.svg` 和 `/logo.png` 都返回本地安全图片资源。
- **上传解析隔离不足（高）**：附件解析子进程此前没有 Linux CPU、地址空间和文件大小限制，并继承服务进程环境。现在限制为 1 GiB 地址空间、35/40 秒 CPU、80 MiB 单文件输出，并只继承必要的临时目录、PATH 和 Python I/O 环境变量。
- **解析进程异常可能变成 500（中）**：解析器被资源限制杀掉时，父进程对空输出直接 `json.loads`。现在转换为稳定的 422 文件解析错误。
- **KaTeX 依赖存在已知原型污染公告（中）**：统一覆盖到 KaTeX 0.19.0；`npm audit --omit=dev` 已报告 0 vulnerabilities，正式构建通过。

## 已验证

- 官方后端 unittest：64 项通过。
- model-chat 核心 unittest：60 项通过。
- 根前端 TypeScript/Vite 构建：通过。
- 正式版 Vite 构建：通过。
- 浏览器静态回归检查：通过。
- 全新 Linux 虚拟环境安装：通过；`pip check` 无坏依赖，`pip-audit` 无已知漏洞。
- 隔离数据库完整性检查：SQLite `integrity_check=ok`。
- 实际启动后健康接口、首页、图标、恶意 Origin 和错误 Host：分别得到预期的 200、200、200、403、403。

## 上线前仍需处理

- 当前正式架构是单进程、单 worker、SQLite WAL 和内存调度器；不能直接横向扩容或增加 worker。面向所有用户前应使用 PostgreSQL、Redis 队列/租约、对象存储和独立解析/推理网关，或者明确单机容量与备份策略。
- 注册默认开放，但没有邮箱验证、找回流程或 CAPTCHA。公网开放注册前应接入验证和防滥用策略；在此之前建议关闭 `registration_open`。
- 尚未对真实模型、搜索和语音服务做在线压测或调用验收，以避免在审计阶段产生费用；上线前需要在目标网络中做一次小额健康检查、限时、取消和失败重试验证。
- 公网发布必须提供真实域名、HTTPS 证书、`WUYU_ALLOWED_ORIGINS`、`WUYU_SECURE_COOKIE=1`、持久化数据卷和备份计划。模型端口不得暴露到公网。
- 上传包包含真实数据库、vault key、用户资料和服务凭据，不能把原始目录或原始 ZIP 公开发布。
