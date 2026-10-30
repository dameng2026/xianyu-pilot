# 个人桌面端改造：存储、卡密与本地发货状态机

改造基于原项目的 Python 后端、SQLAlchemy 模型和 Vue 页面，继续沿用账号、商品、订单、卡密和发货流程。广告、付费投放、反馈工单和商业版桥接已删除；AI、QQ/MCP 仍按需保留。本分支不使用 Docker。

## 当前可验证的内容

- `app.main` 根据显式运行模式选择启动流程。桌面流程不加载原服务端启动器，不连接 MySQL/Redis，不启动平台连接、定时任务或自动发货。
- 所有本地业务文件固定放在程序根目录的 `data/` 内：SQLite 为 `data/app.sqlite3`，上传文件为 `data/uploads/`，历史演示为 `data/demo/`。源码运行时使用项目根目录，打包后使用 EXE 所在目录；不受当前工作目录影响，也不使用 EXE 临时解压目录。
- 首次启动初始化原模型对应的账号、授权、运行状态、商品、订单、订单明细、发货规则、卡密组、卡密和发货记录表。主键在 SQLite 使用 `INTEGER`，保留原 MySQL 类型。
- 迁移按版本和 SHA256 记录，事务执行。拒绝覆盖其他应用的数据库、篡改过的迁移和更新版本的数据库；同一数据目录只允许一个实例。
- 数据库连接启用 WAL、FULL 同步、内存临时存储、外键检查和 5 秒锁等待。写入使用短事务，业务状态与库存一起提交，事务中不等待网络，尚未启动后台真实发送任务。
- 新增 `002_inventory_rules.sql`，补齐原卡密页面使用的分组字段与索引。活跃卡密按“分组 + 卡号 + 密码”唯一，空密码与 NULL 视为同一项；批量请求内和重复请求均会去重。迁移不会修改 `001` 或静默删除旧数据；若旧库已有重复卡密，升级整体回滚，需保留备份后核查。

**当前为官方核验与资产安全阶段，尚非完整桌面产品。** `003_delivery_state.sql` 增加库存占用、逐段回执与审计；`004_official_delivery.sql` 增加权威 SKU 规则、官方事实、IM 线索和触发授权；`005_delivery_safety.sql` 增加永久绑定、只读快照和阶段 B 补偿幂等记录。普通启动仍离线，不对 HTTP 开放模拟付款或伪造平台核验。受信宿主可显式装配双监听及真实网关，启用条件见 [官方订单核验说明](official-order-delivery.md)。桌面主入口仍返回状态 JSON，构建 Web 后新增 `/desktop/safety` 安全工作台，其余原 Vue 业务页面尚未挂载。操作边界见 [资产安全与恢复](delivery-safety-recovery.md)。

## 启动验证

以下脚本仍为源码后端开发入口；Windows 桌面宿主与 GitHub 下载依赖、打包 EXE 的新路径见 [Windows 便携说明](windows-portable.md)。

使用 Python 3.11，在项目根目录准备环境：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install --require-hashes -r apps/api/requirements.txt
.\start-desktop.bat
```

Linux/macOS 的安装解释器使用 `.venv/bin/python`，启动使用 `sh ./start-desktop.sh`。

启动参数示例：

```powershell
.\start-desktop.bat --port 15181
```

默认访问 `http://127.0.0.1:15177/health/ready` 查看数据库和迁移状态，`/api/desktop/status` 查看适配阶段。服务只绑定回环地址，拒绝外站 Origin。终端按 Ctrl+C 退出。不会自动打开浏览器或发送消息。

数据位置固定，不再提供 `--data-dir` 参数，旧的 `DESKTOP_DATA_DIR`、`XYA_DESKTOP_DATA_DIR`、`UPLOADS_DIR` 和 `STORAGE_DIR` 环境变量不会改变目录。程序目录必须可写；不可写时启动报错，不会回退到 AppData 或其他位置。`data/` 已被 Git 忽略，不进入源码发布包。移动整个程序目录时一并保留 `data/`。

旧版本数据不会自动覆盖到新位置。如曾在用户目录保存数据，应先停止旧实例、备份后，将原正式数据目录的内容复制到程序根目录的 `data/`；历史演示数据则放在 `data/demo/`。

桌面流程不读取服务端 `.env`、服务端密钥文件或 MySQL/Redis 环境配置。AI、平台和其他配置将在相关原业务适配时接入。

## 已开放的原业务接口

接口复用原表和前端 camelCase 字段，以及 `{code,msg,data}` 响应格式。无需服务器 JWT/Redis，只接受本机访问；写入请求必须带 `Content-Type: application/json` 或原前端统一设置的 `X-Request-Id`，兼容无请求体的锁定、重置和删除。拒绝跨站 Origin、跨站浏览器请求和 HTML 表单。卡密响应禁止缓存。本机其他进程仍可调用这些接口；完整桌面会话认证尚未实现。

| 接口 | 当前能力 |
| --- | --- |
| `GET/POST /api/cards` | 分组分页查询、新建，创建成功返回分组 ID |
| `GET/PUT/DELETE /api/cards/{id}` | 分组详情、修改、软删除；修改保留未提供的配置字段 |
| `GET /api/cards/alerts` | 低库存提醒（基于 available_count 预警阈值快速过滤） |
| `POST /api/cards/import/validate` | 预检 `items`，返回有效、无效及批内重复数量；不写库 |
| `GET/POST /api/cards/{id}/items` | 明细分页及状态过滤、添加单条卡密 |
| `POST /api/cards/{id}/items/batch` | 最多 5000 条，返回 `successCount/duplicateCount/failCount` |
| `DELETE /api/cards/{id}/items/{itemId}` | 软删除未使用且未锁定、未认领卡密 |
| `POST /api/cards/{id}/items/{itemId}/reset` | 将未使用、未被任务认领的卡密恢复可用 |
| `POST /api/cards/{id}/items/{itemId}/lock`、`/invalid` | 手动锁定、作废 |
| `GET /api/cards/{id}/stats`、`/usage`、`/export` | 库存统计、使用记录、纯文本轻量导出（支持状态过滤，最多 50000 条） |
| `GET/POST /api/auto-delivery/rules` | 原 `delivery_rule` 表规则分页与关键词查询、新建 |
| `PUT/DELETE /api/auto-delivery/rules/{id}` | 完整替换规则、软删除 |
| `GET/PUT /api/auto-delivery/goods/{id}/config` | 同一权威规则的商品/SKU 编排入口与版本校验 |
| `GET /api/orders`、`/api/orders/{id}` | 原订单、明细与核验来源，只读 |
| `GET /api/auto-delivery/records`、`/api/auto-delivery/records/{id}` | 本地任务、逐段状态、回执及审计；支持 `needsReview=true` 待核对过滤；不返回卡密明文 |

批量导入先校验整批内容，存在空卡密或无效字段则整批拒绝，不会部分入库；数据库中途失败也整批回滚。状态继续使用 `0=可用、1=锁定、2=已使用、3=无效、4=错误`。已使用或带有任务认领/订单关联的卡密不能重置、修改或删除，避免重新入库。被规则引用、包含锁定/已使用/认领卡密的分组不能删除。

旧规则 CRUD 契约继续保留，并支持 `plan/trigger/skuId`。新规则默认 `status=0`。商品 JSON 配置与旧规则均经同一规范存取；订单只读、批量规则未开放。普通启动未装配真实网关，保存“启用”不等于程序立即开始真实发货。

重启旧进程前先退出并备份整个 `data/`。重新运行 `start-desktop.bat` 后，健康检查应出现：

```json
{
  "phase": "verified-delivery-config",
  "schema": {"current": true, "version": "004_official_delivery.sql"},
  "businessRoutesEnabled": true,
  "enabledBusinessModules": ["cards", "delivery-rules", "goods-config", "orders-read", "delivery-records-read"],
  "deliveryExecutionMode": "offline-tests-only",
  "automaticDeliveryEnabled": false,
  "realPlatformConnections": false
}
```

可用 PowerShell 在本机验证（会创建一个测试分组及两条虚构卡密）：

```powershell
$api = 'http://127.0.0.1:15177/api'
$created = Invoke-RestMethod "$api/cards" -Method Post -ContentType 'application/json' -Body '{"groupName":"local-test"}'
$groupId = $created.data
Invoke-RestMethod "$api/cards/$groupId/items/batch" -Method Post -ContentType 'application/json' -Body '{"items":[{"content":"test-a"},{"content":"test-a"},{"content":"test-b"}]}'
Invoke-RestMethod "$api/cards/$groupId/stats"
# 预期 successCount=2，duplicateCount=1；库存 totalCount=2、remainCount=2。
```

自动回归使用隔离临时数据库，不操作真实 `data/`：`python -m pytest apps/api/tests/test_desktop_foundation.py apps/api/tests/test_desktop_inventory.py apps/api/tests/test_desktop_delivery.py apps/api/tests/test_portable_runtime.py -q`。

启动恢复在持有单实例锁时，将遗留发送中步骤标为结果未知，保留原订单占用。原服务端“未知超时转可重试”和“终态自动释放卡密”两条恢复路径已关闭；需要人工依据正面证据核对。Mock 完成不改变订单为真实平台已发货。

当前只支持单商品/单 SKU 订单；数量 1–1000，最多一个卡密段，每件一条不同卡密。说明默认按单一次，可按件展开。卡号和密码独立渲染；图片引用相对 `assets/` 并记录哈希，修改素材后原任务停止该段。SKU 精确匹配与编排契约已建立，原商品配置 UI 统一留至阶段 2。

## 下一步实施顺序

1. 已完成原卡密仓库与 `delivery_rule` CRUD 的首批 SQLite 接口及回归；原商品 JSON 发货配置仍待适配。
2. 本地状态机、统一商品 JSON/规则入口和官方核验双监听适配已实现；接下来需验证真实接口契约、账号凭据与会话解析，再接原页面和素材导入。
3. 将原账号、消息和平台连接改为本地流程，替换 Redis 在单用户模式下的会话、锁和调度用途，再删除剩余服务端依赖。
4. 挂载原 Vue 页面并做本机完整链路回归，最后再制作桌面窗口、安装包和更新机制。

升级前停止程序并备份整个数据目录。新增迁移文件，不修改已发布迁移。当前不会导入旧演示数据或自动迁移 MySQL 数据。
