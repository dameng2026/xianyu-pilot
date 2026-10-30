# 闲鱼助手 · 个人桌面端改造

基于原项目已有的 Vue 后台和 Python 业务增量修改，聚焦闲鱼自动发货与 Windows 绿色便携版，保留账号、商品、消息、订单、卡密及日志。本轮不接入外部聊天机器人。

产品方向为单用户本机运行、SQLite 存储，不使用 Docker。广告、付费投放、商业桥接和对应页面、配置已经移除。

## 当前进度

当前尚未完成的开发、联调和实机验收，统一见 [项目进度与未完成清单](docs/project-status-and-remaining-work.md)。

- 原有自动发货和卡密操作继续沿用原业务设计。
- CI 在 Linux/Windows 本机检查和构建，发布可校验的源码部署 ZIP。
- SQLite 已支持卡密与规则接口、只读订单与发货记录，以及离线测试中的整单占用、逐段回执、未知锁定和重启恢复。完整 Vue 业务页面与真实发货尚未接入。
- 阶段设计与安全验收见 [自动发货与便携版设计](docs/desktop-delivery-design.md)。普通启动仅提供本地配置与查询，HTTP 不提供模拟付款、伪造平台核验或绕过验证的发货入口。
- 新增 [官方订单核验与双监听](docs/official-order-delivery.md)：统一商品/SKU 规则、严格付款状态、关键词核验与实时/20 秒轮询入口。真实运行通过受信宿主显式装配；普通桌面启动默认离线，待真实接口契约及账号接入验证后启用。
- 新增 [资产安全与恢复工作台](docs/delivery-safety-recovery.md)：数据库禁止释放未知卡密、只读内容与图片快照、启动/重连对账、阶段 B 单独补偿。构建 Web 后可访问 `/desktop/safety`，查看红色待核对警告和分段状态；真实平台未装配时保持只读。
- 新增 [Windows 便携宿主与 GitHub 打包](docs/windows-portable.md)：EXE 目录数据闭环、内嵌 Chromium、关闭驻留托盘、暂停/恢复及电源事件桥接。依赖由 GitHub Windows runner 下载，成功后输出 `xianyu-pilot-windows-portable` ZIP；本地尚未构建 EXE，真实托盘、干净虚拟机及 I/O 验收仍待完成。
- 原服务端流程暂时仍依赖 MySQL/Redis，供未迁移的原业务开发使用。待相关业务适配完成后删除这些依赖。

SQLite 存储基础的安装、启动和后续顺序见 [桌面改造说明](docs/desktop-sqlite-foundation.md)。Windows 准备好 Python 依赖后运行 `start-desktop.bat`，默认状态入口为 `http://127.0.0.1:15177/health/ready`。

本地数据固定保存在程序根目录的 `data/` 下，数据库为 `data/app.sqlite3`，上传文件为 `data/uploads/`。移动程序时保留该文件夹即可；不使用 AppData 或自定义数据目录。

桌面启动器会检查 `data/` 及 `assets/profiles/logs/tmp/backups`，将 Python 临时文件定向到 `data/tmp/`。目录不可写或有逃逸链接时停止并在 Windows 显示错误；完整浏览器、托盘和 EXE 便携性验收留至打包阶段。

## 开发与发布

- [原本机开发流程](docs/local-dev-runbook.md)：`start-local.bat` / `start-local.sh`，适用于迁移中的原服务端。
- [本机源码部署包](docs/native-deployment.md)：包含原源码和本次验证的 Web/Crawler 构建，尚非 EXE 安装包。
- [流水线规则](docs/ci-pipeline.md)：检查、构建、安全审计和受限的版本发布。
- [自动发货增量改造](docs/auto-delivery-incremental.md)：原业务修改记录。

历史独立工作流演示保留在 `apps/desktop`，不作为此次桌面改造的业务实现，也不进入原系统发布包。相关历史文档保留供追溯。

项目包含上游开源代码；许可证和署名见 [LICENSE](LICENSE) 与 [NOTICE](NOTICE)。
