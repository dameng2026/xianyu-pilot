# 原系统本机部署包

本分支的 CI 直接使用 Python、Node.js 进行检查、构建和发布，不使用 Docker、容器作业或容器服务。

个人桌面端已加入 SQLite 存储基础入口 `start-desktop.bat` / `start-desktop.sh`，无需启动 MySQL/Redis；当前只提供存储与状态接口，业务接口尚在适配。具体步骤和边界见 [桌面存储基础](desktop-sqlite-foundation.md)。下文 `start-local` 指尚待迁移的原服务端开发流程。

## 下载的是什么

Actions 产物 `xianyu-pilot-native-source` 中有源码部署 ZIP 和 `SHA256SUMS.txt`。ZIP 包含：

- 原有 `apps/api` 后端和数据库迁移。
- 原有 `apps/web`、`apps/crawler` 源码及本次 CI 已构建的 `dist`。
- 原本机启动、停止、状态和初始化脚本。
- 环境配置示例、许可证、使用说明和文件校验信息。

不包含真实 `.env`、账号凭据、业务数据、数据库备份、依赖缓存或独立桌面演示。包内 `package-info.json` 标明源码提交、Actions 运行编号及每个文件的 SHA256。

这是本机部署源码包，不是自带全部运行环境的 EXE，也不是离线安装包。

## 运行环境

先在本机准备：

| 依赖 | 版本 |
| --- | --- |
| Python | 3.11 |
| Node.js / npm | 22.23.1 / 10.9.8，与现有锁文件一致 |
| MySQL | 8.0，使用本机独立数据库和用户 |
| Redis | 7，使用本机服务 |

首次启动沿用原初始化脚本安装 Python/Node 依赖及 Playwright 浏览器，需要网络；Linux 的浏览器系统依赖也需由本机提供。流水线打包不会把 MySQL、Redis 或浏览器安装到用户电脑上。

## 使用步骤

1. 核对 ZIP 的 SHA256，与附件 `SHA256SUMS.txt` 一致。
2. 解压到新的目录。升级前停止旧实例，备份原数据库、上传内容和本机配置；不要直接覆盖正在运行的安装。
3. 确认本机 MySQL、Redis 可用。第一次初始化会创建配置、密钥、数据库用户和安装依赖，先阅读 [本地运行说明](local-dev-runbook.md)。
4. Windows 执行 `start-local.bat`；Linux/macOS 执行 `sh ./start-local.sh`。
5. 访问 `http://127.0.0.1:15176`。用 `status-local.bat` / `stop-local.bat` 管理 Windows 实例，其他平台使用同名 `.sh` 脚本。

本机流程沿用原系统的账号、订单、自动发货、卡密仓库与发货记录，不依赖 QQ 或 AI 才能启动。是否执行自动发货仍由原商品和账号配置决定。

既有启动脚本可能重新构建 Web/Crawler，这是它们原有的本机开发行为；ZIP 附带的 `dist` 用于记录本次已验证构建，不代表已另写生产静态服务启动器。

## CI 验证边界

流水线验证现有 Linux/Windows 后端回归、Web 单元测试、Web/Crawler 构建、安全审计，以及部署包的源码归属、文件完整性和路径安全。它不登录真实账号，不连接用户的数据库，也不向真实订单发货。

这不等同于完成任意电脑上的 MySQL/Redis 安装和真实业务联调。
