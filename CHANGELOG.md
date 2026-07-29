# 更新日志

本项目所有显著变更均记录于此文件。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，遵循语义化版本 [SemVer](https://semver.org/lang/zh-CN/)。

- **主版本号**：不兼容的破坏性变更
- **次版本号**：向下兼容的新功能
- **修订号**：向下兼容的问题修复

维护规则见 `.trae/rules/changelog-update.md`：每次发布必须将 `[Unreleased]` 转为 `[vX.Y.Z] - YYYY-MM-DD`、同步更新 `apps/web/package.json` 的 `version` 字段、在顶部新增空 `[Unreleased]` 段。

## [Unreleased]

### 新增
- _暂无_

### 变更
- _暂无_

### 优化
- _暂无_

### 修复
- _暂无_

## [v1.6.0] - 2026-07-29

### 新增
- **退款管理模块**：新增「退款管理」路由与服务（`refunds.py` / `refund_service.py`），支持退款分页列表查询（账号、状态、关键词筛选）、退款统计（按 `order_status` 分组）、退款详情查看、按账号或全量同步退款订单、账号同步状态列表；新增迁移 039 `xianyu_refund` / `xianyu_refund_account_state` 两表（退款 ID 维度，一个订单可对应多次退款，包含商品信息、退款金额、退款状态、客服介入状态、物流信息等字段）
- **小刀订单免拼发货**：`xianyu_api_service.py` 新增 `confirm_freeshipping` 调用 `mtop.idle.groupon.activity.seller.freeshipping` 免拼发货接口（小刀订单专用），并新增 `confirm_order_shipment` 统一调度函数（小刀订单走免拼、普通订单走虚拟发货）；`order.py` 新增免拼发货接口与 `ConfirmFreeshippingReqDTO`；已发货（`ORDER_ALREADY_DELIVERY`）视为幂等成功
- **订单同步对接真实流程**：`/syncSoldOrders` 由原占位实现改为调用 `sync_orders_for_account` 真实拉取 `mtop.taobao.idle.trade.merchant.sold.get` 并 upsert 本地，返回真实的 `synced_count` / `inserted` / `updated` / `failed` 计数
- **关于我们新增 QQ 群与微信客服板块**：「关于我们」页新增「QQ 群」「微信客服」两个社区卡片，配置后可展示二维码；前端 `about-content-model.js` 增加对应数据模型，`AboutSettings.vue` 新增 violet 色调样式；README 同步新增 QQ 群与微信客服二维码入口
- **通知渠道密钥清除按钮**：通知设置页所有敏感字段（SMTP 授权码、飞书 App Secret、Verification Token、Encrypt Key、Webhook URL、签名密钥）在「已配置」状态下显示清除按钮，点击后标记清除并提示「保存后生效」，解决之前无法清空已保存密钥的问题
- **人工 OUT 消息暂停自动回复**：`ws_startup.py` 新增人工 OUT 消息检测，卖家手动发送消息（`direction=OUT` 且 `is_auto_reply=0`）时自动将该会话 `auto_reply_paused=1` 并记录 `last_manual_reply_at`，避免 AI 自动回复与人工回复冲突；AI 回复消息不触发暂停

### 变更
- **密码强度策略放宽**：`security.py` 密码最低长度从 12 位降为 6 位，移除「大写字母、小写字母、数字、特殊字符至少三类」约束；个人中心页同步将提示文字从「至少 8 位」调整为「至少 6 位」，降低开源用户部署门槛
- **审计与桥接能力默认开启**：`docker-compose.yml` 中 `AUDIT_MUTATION_INTENT_REQUIRED` 与三个商业版桥接能力 flag（`COMMERCIAL_BACKEND_MUTATION_IDEMPOTENCY_ENABLED` / `PAYMENT_IDEMPOTENCY_ENABLED` / `PAID_AD_PLACEMENT_ENFORCED`）默认值从 `false` 改为 `true`，与开源版桥接规则要求的三开关默认全开一致
- **Secret 文件权限收紧**：`setup-wizard.sh` 生成的所有 secret 文件权限从 `644` 改为 `600`，避免其他用户读取密钥
- **顶栏精简**：`Topbar.vue` 移除通知中心、关于我们、全屏切换三个按钮及其面板与事件监听，仅保留头像与退出登录菜单
- **请求 ID 优先级调整**：`request.js` 优先使用请求 config 中的 `X-Request-Id`，其次响应头与响应体，便于在请求发起侧统一追踪
- **开源版版本号升至 1.6.0**：本次新增退款管理、免拼发货、QQ群/微信客服板块、人工OUT暂停自动回复等多项用户可见功能，按语义化版本次版本号 +1，从 1.5.0 升至 1.6.0

### 优化
- **自动发货数据库层去重保护**：`ws_delivery_handler.py` 新增 `_has_existing_realtime_delivery`（查询 `delivery_record` 表，成功记录 10 分钟窗口 / 失败记录 1 小时窗口）与 `_check_order_already_shipped`（检查 `xianyu_trade_order.order_status=3`）两道去重防线，防止 WS 周期性推送同一付款事件导致重复发货；新增付款兜底节流（同 `account_id + pnm_id` 60 秒内只触发一次），覆盖 WS 事件丢失、可重试错误失败、启动遗漏等场景
- **消息去重 ID 标准化**：`ws_storage.py` 新增 `_normalize_id_for_hash`，标准化 `sender_user_id` / `receiver_user_id`（去除 `sid:` 前缀与 `@goofish` 后缀）后参与 `content_hash` 计算；SQL 兜底匹配用 `REPLACE` 兼容旧记录中带后缀的字段；`ws_protocol.py` 同步标准化逻辑；解决 API 发送（带后缀）与 WS 回环（无后缀）生成不同 hash 导致去重失败、前端重复显示的问题
- **SSE 独立连接配额**：`nginx-main.conf` 新增 `sse_connections_per_ip` zone，`nginx.conf` 让 `/api/sse/` 长连接使用独立 conn zone，避免页面加载时并发的短连接请求占满 SSE 配额导致 429
- **API 就绪探针兼容 Python 3.10**：`main.py` 将 `asyncio.timeout` 改为 `asyncio.wait_for` 包装，兼容 Python 3.10（`asyncio.timeout` 为 3.11+ API）
- **TimeoutError 异常类型修正**：`worker.py` / `scheduled_task_runtime.py` / `message_automation_outbox.py` 将 `except TimeoutError` 改为 `except asyncio.TimeoutError`，修复 Python 3.10 下裸 `TimeoutError` 捕获不到 `asyncio.wait_for` 超时的问题
- **start.bat 端口查找范围判断修正**：原 `if !TRY_PORT! GTR !WEB_PORT!+9` 在 `set /a` 表达式外无法解析，改为先 `set /a MAX_PORT=!WEB_PORT!+9` 再比较，修复端口冲突时自动查找可用端口逻辑失效
- **start.sh 数据库迁移等待时长**：迁移等待循环从 60 秒增加到 90 秒，避免慢机首次迁移超时被误判失败

### 修复
- **在线消息重复显示（API/WS 回环 hash 不一致）**：API 发送消息时 `senderUserId` 带 `@goofish` 后缀，WS 回环消息为原始 ID，导致同一条消息生成不同 `content_hash`，去重失败，前端显示两条；现标准化后再哈希，DB / SSE 广播 / WS 回环使用同一 `messageIdentity`；`MessagesPage.vue` 同步增加 `pnm_id` / `message_uid` 字段读取兜底
- **同步订单接口返回空数据**：`/syncSoldOrders` 之前硬编码返回 `synced_count: 0`，未调用真实同步流程；现对接 `sync_orders_for_account` 后返回真实插入/更新计数

## [v1.5.0] - 2026-07-25

### 新增
- **商品位置改用本地行政区划数据**：移除高德地图 API 依赖，内置全国省/市/区行政区划 JSON 数据（34 省 / 344 市 / 3104 区县），开源版开箱即用无需配置 API Key；后端 `/address-dict/tree` 端点改为读取本地 `china_address_dict.json`，与商业版 `china_address_dict` 接口返回结构保持一致
- **远程滑块求解预检验 UI 改进**：开启远程滑块求解服务时，预检验失败原因动态展示在「远程滑块求解服务」板块上方，支持多条提示同时显示，用户可明确知道哪项预检验未通过（API 链接格式、密钥非空、商业版后端连通性）；移除 API 链接输入框的复制按钮
- **远程滑块求解服务后端与记录**：新增 `remote_slider_config` / `remote_slider_record` / `remote_slider_solver` 三个后端服务，统一管理远程滑块求解 API 配置、调用记录与求解执行；新增迁移 037 `xianyu_remote_slider_solve_record` 表（append-only，记录 request_id / trigger_scene / status / token_charged 等字段）；前端新增「远程滑块求解 API」配置页（`RemoteSliderApiPage.vue`）与 `remoteSlider.js` API 模块
- **发货确认语句会话**：新增迁移 038 `delivery_statement_session` 表，按订单跟踪「确认前发货」语句会话生命周期（declaring / waiting / confirmed / cancelled）；新增 `ws_statement_handler.py` 发货语句处理器，支付成功后发送语句并创建会话，买家回复"确认"后才触发实际发货；`realtime_delivery.py` 与 `delivery_recovery.py` 集成语句会话判断，避免未确认订单被误发货
- **自动滑块求解跨模块去重**：`captcha_solver.py` 新增 `should_auto_solve` / `mark_auto_solve_started` 状态机，同账号 10 分钟内只自动求解一次，避免断线重连循环、Cookie 保活、心跳停跳等多个触发源同时发起求解导致重复扣费
- **开源版桥接与防暴露规则文件**：新增项目规则文件 `.trae/rules/changelog-update.md`（更新日志维护流程）、`.trae/rules/opensource-commercial-bridge-sync.md`（开源版与商业版桥接同步约束）、`.trae/rules/opensource-no-commercial-exposure.md`（开源版前台不得暴露商业版 IP/后台地址/token），固化开源版发布前的强制检查流程
- **模型配置图片提示词迁移增强**：迁移 036 增加 `status`（1启用/0禁用）与 `updated_time` 字段，并增加防御性数据回填（`prompt_name <- name`、`prompt_content <- prompt_template`、`params_json <- 旧版 image_size + quality`），兼容旧版 001_init 部分迁移的数据库
- **单元测试目录**：新增 `apps/api/tests/`（`test_address_dict_runtime.py` / `test_captcha_solver_regression.py` / `test_commercial_bridge.py`）与 `apps/web/test/`（`mobileMessagesSafety.test.js`）单元测试，覆盖行政区划数据运行时校验、滑块求解回归、商业桥接、移动端消息安全等场景

### 变更
- **移除项目内高德 API 相关配置与数据**：删除 `AmapSettingsPage.vue` 高德地图配置页、`amap_router` POI 搜索路由、`amap_api_key` 环境变量与系统配置项；商品位置数据源由高德行政区划 API 改为本地 `china_address_dict.json` 文件；导航与系统配置页同步移除「高德地图」入口
- **发布商品页移除冗余说明文字**：移除「选择省 / 市 / 区后将以下结构化字段（poiName / prov / city / area / divisionId / gps / poiId）提交至闲鱼发布接口，与商业版发布逻辑保持一致」的技术细节说明，简化用户界面
- **开源版版本号升至 1.5.0**：本次新增远程滑块求解服务后端、发货确认语句会话、自动求解去重等多项用户可见功能，按语义化版本次版本号 +1，从 1.4.0 升至 1.5.0

### 修复
- **本地滑块求解 500 服务器错误**：`/api/captcha/handle` 调用本地滑块求解时，`decrypt_cookie_if_needed` 在密钥不匹配或数据损坏时抛出未捕获的 `RuntimeError`，导致接口返回 500；现已在 `try_auto_solve` 中捕获异常并返回结构化错误 `CAPTCHA_COOKIE_DECRYPT_FAILED`，引导用户重新扫码登录，不再泄露服务器内部错误
- **在线消息页面重复消息**：单条消息 '1' 曾显示为三条（数据库 id=1576 有效 pnm_id + id=1577 NULL pnm_id 旧逻辑产物 + 前端乐观 UI）；`ws_storage.py` 修复 NULL pnm_id 处理逻辑，新增 `s_id + sender + receiver + content` 精确匹配兜底，并在 `misc.py` 先入库后 WS 回环场景下用真实 pnmId 覆盖数据库兜底哈希，确保 DB / SSE 广播 / WS 回环使用同一 messageIdentity

## [v1.4.0] - 2026-07-24

### 新增
- **闲鱼账号一键滑块求解**：账号管理页表格操作列与账号详情页"快捷操作"区新增"滑块求解"按钮，支持对单个账号手动触发滑块验证重试，解决账号 Cookie 触发风控时无法自动恢复的问题；求解中显示"求解中"状态，求解成功后自动刷新账号列表
- **远程滑块求解服务全局接管**：开启"远程滑块求解服务"后，系统内所有滑块求解（账号登录、消息收发等场景）统一改走远程 API，不再调用本地浏览器求解；修复了 engine 字段硬编码导致开启远程后仍记录为本地求解的问题
- **远程滑块求解预检验**：开启远程滑块求解服务前强制预检验 API 链接与密钥：校验链接格式（http/https + 主机名 + 路径）、密钥非空、调用远程接口连通性，任一不满足则禁止开启并返回明确错误提示，避免配置错误后所有求解请求失败
- **Token 消费统计对接商业版真实数据**：远程滑块求解页面的 Token 消费统计改为基于商业版远程 API 实时返回的扣费结果（tokenCharged / tokenChargeFailed）计算，仅"成功求解且扣费成功"才计入 Token 消耗，扣费失败不计入；新增"数据来源"说明，明确统计口径
- **商品位置三级联动（同步商业版）**：发布商品页"商品位置"由高德 POI 搜索改为省/市/区三级联动下拉选择，与商业版发布逻辑保持一致；选择后透传结构化字段（poiName / prov / city / area / divisionId / gps / poiId）至闲鱼 MTOP 发布接口；后端新增 /address-dict/tree 端点，内存缓存 7 天；草稿兼容旧版高德 POI 格式
- **关于我们页面版本与更新日志规则**：新增项目规则文件 .trae/rules/changelog-update.md，强制要求每次更新必须详细描述本次更新内容、与代码改动保持一致、版本号与 package.json 同步；"关于我们"页面当前版本来自 package.json，更新日志来自 CHANGELOG.md，二者必须保持一致

### 变更
- **开源版版本号升至 1.4.0**：本次新增多项用户可见功能（账号滑块求解、远程滑块全局接管、远程滑块预检验、Token 统计对接、商品位置三级联动），按语义化版本次版本号 +1，从 1.3.0 升至 1.4.0
- **系统配置页隐藏商业版后台地址**：开源版"商业版桥接状态"板块不再展示商业版后台 URL，仅保留商业版前台 URL 用于引流；后端 `/system/runtime-status` 与 `commercial_bridge.get_commercial_bridge_runtime` 同步移除 `commercialAdminUrl` 字段返回（保留 `commercialFrontendUrl`），避免开源用户通过浏览器获取商业版后台地址；同时 `commercialBridgeMessage` 中的 http(s) URL 统一脱敏为 `[已隐藏]`，防止 httpx 异常消息泄露商业版后端 origin

## [v1.3.0] - 2026-07-22

### 新增
- **自动发货补发兜底循环**：worker 新增每 10 分钟（可配置）扫描已开启自动发货但未发出的订单，复用 RealtimeDeliveryCoordinator 的幂等状态机安全补发；覆盖 WS 事件丢失、可重试错误失败、启动遗漏等场景；新增 `POST /auto-delivery/recover` 手动触发接口与前端"立即补发未发货订单"按钮；新增 `DELIVERY_RECOVERY_*` 环境变量配置开关、间隔、批量、最小订单年龄
- **广告申请支付功能接入**：开源版广告申请页面经商业版桥接服务中转连接商业版后端，开源版不直接接触商业版 IP；支持易支付（yipay）微信扫码支付，返回真实 zpayz.cn 支付二维码与 base64 图片；申请意图与支付下单双幂等键（LocalStorage 持久化），支持失败安全重试；商业桥接 fail-closed 三能力 flag（mutation_idempotency / payment_idempotency / paid_ad_placement）全部通过才解锁提交；新增"公司或主体名称"必填字段（前端模型、UI、后端校验三层联动）
- **自动回复范围管理**：新增 `auto_reply_scope` 路由模块，支持会话级别的自动回复开关控制，可针对单个会话启停自动回复
- **送货工作流兼容性增强**：`delivery_workflow_compat` 路由扩展发货工作流兼容接口，补齐发货配置与文本源相关端点
- **自动发货补发恢复服务**：新增 `delivery_recovery.py` 服务，worker 集成定期补发扫描逻辑，覆盖 WS 事件丢失与启动遗漏场景
- **数据库迁移 031/032**：新增 `031_conversation_auto_reply_state`（会话自动回复状态持久化）、`032_delivery_text_source_card_mode`（发货文本源卡片模式）两份版本化迁移脚本
- **国内镜像源加速（阿里云 ACR）**：GitHub Actions 构建镜像后用 `docker buildx imagetools create` 将多架构 manifest 同步到阿里云 ACR 个人版（不重新构建，秒级完成）；`docker-compose.yml` 三个服务默认镜像源改为 ACR（国内拉取快），GHCR 保留为海外备用源，可通过 `.env` 的 `IMAGE_*` 变量切换；`start.sh` / `start.bat` 镜像源连通性检测目标同步改为 ACR（401/403 也视为可达）；ACR 仓库设为公开，开源用户无需 `docker login` 即可直接拉取

### 变更
- **商品管理页面重写**：移除擦亮功能（删除 `item_polish.py` 及前端 `ItemPolishConflictCard` / `ItemPolishUnknownReconcile` / `useItemPolish` / `itemPolishState` 共 2134+ 行代码）；修复商品封面图显示（9 字段兜底 + 协议修正）；库存逻辑改为默认 999（获取不到真实库存或为 0 时）；曝光/浏览/想要数据兼容多版本字段并从多源头提取；商品同步逻辑对齐商业版
- **首页轮播图位置调整**：轮播图移至顶部"新手三步"板块上方，符合内容优先级
- **.env.example 商业桥接配置**：新增 `COMMERCIAL_BACKEND_BASE_URL`、`COMMERCIAL_BACKEND_ACCESS_TOKEN`、3 个能力 flag（`COMMERCIAL_BACKEND_MUTATION_IDEMPOTENCY_ENABLED` / `PAYMENT_IDEMPOTENCY_ENABLED` / `PAID_AD_PLACEMENT_ENFORCED`）配置项
- **docker-compose 数据保留策略**：新增 MySQL binlog 过期时间配置，避免系统盘被 binlog 撑满

### 优化
- **部署简化：bcrypt 生成彻底重构**：移除 setup-wizard 中 6 层 bcrypt fallback 链（主机 Python → pip install → Docker slim → 国内源 → alpine → api 镜像），改为只创建空文件，由 start.sh / start.bat 在 api 镜像就绪后统一生成（零额外下载，必定成功）；消除 NAS/离线环境最大部署痛点，部署时长缩短 5-15 分钟
- **部署兜底：端口冲突自动选择**：8080 被占用时自动尝试 8081-8089，找到可用端口后自动更新 .env，无需用户手动修改配置
- **部署兜底：磁盘空间预检查**：启动前检查可用空间，不足 5GB 时警告并给出 `docker system prune` 清理建议，避免构建中途失败
- **部署兜底：GHCR 连通性检测**：拉取前 5 秒超时检测 GHCR 可达性，不可达时直接本地构建，避免国内网络下拉取超时浪费数分钟
- **部署兜底：Windows Docker Desktop 自动启动**：检测到 Docker 引擎未运行时自动启动 Docker Desktop 并等待就绪（最长 90 秒），无需用户手动启动
- **部署兜底：分阶段健康检查**：按依赖顺序检查 MySQL → migrate → Redis → API → Web，每阶段显示进度和耗时，总耗时实时反馈
- **部署兜底：失败自动诊断**：任一阶段失败时自动收集容器状态、异常服务最近日志、磁盘空间、端口占用，生成诊断报告，减少用户排查时间

### 修复
- **自动发货后订单状态不更新**：自动发货配置表单缺少"自动确认发货"开关，导致 `autoConfirmShipment` 永远为默认值 0，卡密/文本消息发送成功后既不调用闲鱼平台确认发货接口，也不更新本地订单状态；现已在"自动发货 → 配置 → 高级设置"中增加开关，开启后发送成功会调用平台确认发货并把订单标记为已发货
- **MessagesPage 重复声明**：`toggleConversationAutoReply` import 与本地函数同名导致 Vite 构建报 `Identifier has already been declared`，改为别名导入 `toggleConversationAutoReplyApi`
- **广告申请 companyName 缺失**：商业版后端要求 `companyName` 必填，但开源版前端表单、payload 构建函数、后端校验均未传递此字段，导致申请提交返回 502 "广告申请结果未确认"；已在前端模型、UI 输入框、后端 `_validated_ad_application_payload` 三层补全

## [v1.2.0] - 2026-07-20

### 新增
- **一键启动脚本**：新增 `start.sh`（Linux/macOS）与 `start.bat`（Windows）入口脚本，自动调用初始化向导、拉取镜像、启动 7 个服务并等待健康检查；支持 `--build`（本地源码构建）和 `--no-pull`（跳过拉取）参数
- **首次初始化向导**：新增 `scripts/setup-wizard.sh` 与 `scripts/setup-wizard.ps1`，首次启动自动生成 7 组随机 secrets（MySQL root/app/migration 三组、Redis、JWT、Cookie、Token，均 Base64URL 编码 ≥32 字符）、4 个空的可选 secrets、bcrypt cost 12 admin 密码 hash 和 `.env` 文件；优先用主机 Python，缺失时自动退到 Docker 临时容器生成
- **跨平台运维包装器**：新增 `scripts/production_ops.py`，提供 `status`/`logs`/`stop`/`restart` 四个子命令，限制日志服务名白名单（mysql/redis/migrate/api/worker/crawler/web）和 `--tail` 范围（1-10000），停止命令默认不删除命名卷
- **小刀订单免拼发货**：小刀（砍价）订单自动调用闲鱼免拼发货接口（mtop.idle.groupon.activity.seller.freeshipping）完成发货，而非普通确认发货接口；订单同步时通过 btnList 的 SKIP_PIN 自动检测小刀订单并标记 is_bargain（只置 True 不回退）；自动发货网关根据订单小刀状态智能路由免拼/确认发货接口
- **发布商品页面增强**：运费设置支持包邮/一口价/无需邮寄三模式互斥切换；图片 URL 增加 resolveTrustedMediaUrl 白名单防护（防 XSS）；图片上传增加 imageUploadValidationMessage 预校验（大小≤5MB、MIME 类型、扩展名）；账号选择增加 pickPreferredAccount 智能选择（优先可用账号）
- **在线消息页面客户订单板块**：会话侧边栏新增客户订单卡片（封面、状态徽章、金额、订单详情入口）；新增 getCustomerOrders API；后端 /orders 接口支持 buyerId 过滤
- **发布商品基础设施**：新增 requestLifecycle.js（createRequestGate 请求竞态保护）、imageUploadPolicy.js（图片上传预校验）、publishAddress.js（地址标准化工具）、PublishAddressCascader.vue（三级地址级联选择器）、safeMediaUrl.js（可信媒体 URL 校验）
- **发货记录页面数据完整性**：后端 SQL 补齐 purchase_time/goods_cover_pic/seller_name/seller_display_name/goods_id 字段；JOIN xianyu_account 表获取卖家信息；前端新增商品缩略图列（含 onGoodsThumbError 容错）、卖家列、购买时间列；详情面板新增外部订单号/商品ID/卖家/购买时间字段
- **一键检查 GitHub 更新**：在"关于我们"页新增"版本更新检查"卡片，自动识别 Docker / 源码部署方式，生成对应更新脚本，支持镜像源切换（GHCR / 阿里云 ACR / 离线 tar.gz）和"我已执行完成，刷新页面"按钮；后端新增 `GET /system/update-info`、`POST /system/update-feedback` 端点，带 6 小时缓存和 GitHub API 失败兜底
- **新手部署向导**：新增 `scripts/setup-wizard.sh` 与 `setup-wizard.ps1`，首次启动自动检测 Docker、生成随机 secrets、校验配置、启动服务；`start.sh` / `start.bat` 在缺少 `.env` 或 `./secrets/` 时自动调用向导
- **首次登录引导清单**：`DashboardPage` 顶部接入 `OnboardingChecklist`，通过 `localStorage` 持久化完成状态，支持"不再提示"按钮；自动检查 `/system/runtime-status` 同步模型配置完成情况
- **README 快速上手章节**：新增"3 分钟快速上手"章节，包含前置要求、3 步启动、常见问题表格，新手无需阅读生产部署详细文档即可上手
- **错误文案带下一步建议**：`friendlyError.js` 扩展数据库/Redis/WebSocket/Token 失效/同步失败等错误的文案，直接告诉用户"下一步该怎么做"

### 优化
- **商品管理页面健壮性**：pollSyncProgress 增加连续失败熔断（3次即抛错）与严格响应校验（status 白名单/pct 范围[0,100]/对象类型校验）；init 改为分步容错加载（账号失败不阻塞后续）；loadGoodsStats 严格校验排除 null/undefined/空字符串；syncAllAccounts 进度防倒退（删除每账号 progress=0 重置）；batchDeleteProducts 增加 warnings 分类（remote_confirmed/warn 类型记为需人工核对而非失败）
- **订单管理页面严格校验**：syncCurrentOrder 增加 data.ok 布尔校验与成功/失败分支；selectOrder 增加 id 匹配校验与 ordersAvailable 前置检查，去除 row 回退；新增 detailLoadError 独立错误状态；syncAccountOrders 增加响应格式校验；openManualDelivery 利用 selectOrder 返回值
- **发货记录页面严格验证**：load() 改用 recordsOfOrThrow 替代 recordsOf（异常时抛错而非静默降级为空列表）
- **API 数据工具增强**：apiData.js 新增 recordsOfOrThrow（严格版，异常抛错）；totalOf 增加 Number.isSafeInteger 与负数校验
- **账号鉴权工具增强**：accountAuth.js 新增 pickPreferredAccount（智能账号选择）、accountWsConnectionState（WS 三态）、resolveAccountAuthDisplayState（Cookie+WS 综合状态）、shouldAttemptAccountWebSocketStart

### 修复
- **bcrypt hash 生成兜底链**：NAS 等离线环境运行 `sh ./start.sh` 时 `pip install bcrypt` 静默失败（`set -eu` + `2>/dev/null` 吞掉错误），导致 admin 密码 hash 为空、容器启动 fail-closed；新增 5 层兜底链（主机 Python → pip install → Docker slim → 国内源 → alpine）并以 api Docker 镜像作为最终兜底，同步更新 `docs/deployment-guide.md` Q3 提供 4 种手动解决方案
- **docker-compose secrets 机制修复**：原 `secrets:` 顶层使用 `environment: ADMIN_PASSWORD_HASH` 模式期望主机环境变量为明文，但 `.env.example` 仅配置了 `_FILE` 路径变量，导致 `docker compose up` 时 secret 内容为空触发 fail-closed 启动失败；现统一改为 `file: ./secrets/<name>` 模式，与 `.env.example` 的 `_FILE` 路径完全对齐
- **生产部署默认值修复**：`.env.example` 中 `AUDIT_MUTATION_INTENT_REQUIRED` 改为 `true`（生产预检强制要求），`WEB_BIND_ADDRESS` 改为 `0.0.0.0`（便于局域网访问，原 `127.0.0.1` 导致 VPS 部署后浏览器无法访问）
- **订单同步结果判断 BUG**：syncCurrentOrder 此前忽略 data.ok 字段，同步失败时仍显示绿色成功提示；现改为基于 data.ok 分支显示成功或失败
- **订单详情回退到行数据**：selectOrder 此前在详情加载失败时回退到 row 概要数据当详情展示；现改为严格校验 id 匹配，失败时不回退
- **仪表盘功能特性板块溢出**：在 1501-1680px 中宽屏下，「功能特性」与「快速开始」卡片降为 3 列布局，解决 4 列时单卡过窄导致长描述与「点击进入 XXX」副文本大量换行、超出容器的问题

## [v1.1.0] - 2026-07-15

### 新增
- **Docker 镜像自动构建与发布**：每次推送到 `main` 分支时，GitHub Actions 自动构建 `api`/`web`/`crawler` 镜像并推送至 GHCR（`ghcr.io/xianyu-assistant-opensource/xianyu-assistant-{api,web,crawler}`），支持 `latest` 与 git 短 SHA 双标签
- **一键拉取预构建镜像运行**：`docker compose pull && docker compose up -d`，无需本地源码构建
- **镜像源可覆盖**：通过 `.env` 的 `IMAGE_NAMESPACE`/`API_IMAGE`/`WEB_IMAGE`/`CRAWLER_IMAGE` 切换命名空间或镜像源
- **更新日志机制**：新增 `CHANGELOG.md`，并落地为项目规则，每次上传追加版本记录

### 变更
- `docker-compose.yml` 中 `api`/`migrate`/`crawler`/`web` 服务的 `image` 默认值由本地标签改为 GHCR 路径，同时保留 `build` 字段以便 `--build` 切回本地构建

### 修复
- **商品同步接口异常处理**：`/items/sync-progress/{sync_id}` 与 `/items/syncing/{account_id}` 两个端点增加 try/except 兜底，避免数据库查询或内存进度读取异常时直接返回 500，改为记录日志并返回统一错误响应

## [v1.0.0] - 2026-07-14

闲鱼助手开源版首个正式发布版本。

### 功能亮点
- 🧑‍💼 **闲鱼账号管理** — 多账号接入、二维码登录、状态监控
- 📦 **商品管理与发布** — 上下架、编辑、批量操作、分类
- 🧾 **订单管理** — 同步、跟踪、状态流转
- 💬 **在线消息** — 实时会话、WebSocket 长连接、分页回溯
- 🚚 **自动发货** — 卡密自动交付、实时与手动双通道
- 🎫 **卡密仓库** — 库存管理、去重、交付记录
- 🤖 **自动回复** — AI 驱动、知识库增强、人设与规则可配
- ⏰ **定时任务** — 调度执行、心跳与租约保护
- 📝 **操作日志** — 审计留痕、保留期管理
- 🔔 **通知渠道** — 持久化防重复测试发送，未知结果只能人工确认关闭
- 📚 **RAG 知识库** — 向量检索增强回复
- ⚙️ **系统配置** — 通用模型、向量模型、RAG、商业版桥接状态
- 🧩 **Crawler 滑块求解** — 由 API 同会话维护的二维码登录
- 🏠 **首页运营** — 轮播、公告、文字广告、广告申请、关于我们
- 🔗 **反馈建议** — 向我们反馈功能建议

### 技术架构
- 后端 API：Python 3.11 + FastAPI + SQLAlchemy 2.0
- 前端 Web：Vue 3 + Vite
- 爬虫服务：Node.js 22 + TypeScript + Playwright
- 数据库：MySQL 8.0
- 缓存：Redis 7
- 反向代理：Nginx
- 部署方式：Docker Compose

### 安全特性
- 全套生产秘密通过文件注入（`./secrets/*`，权限 `0600`）
- MySQL 最小权限双账号（迁移账号与运行账号分离）
- 容器 `read_only` + `cap_drop: ALL` + `no-new-privileges` 加固
- JWT 认证、Cookie 加密、CORS 白名单、登录限流
- 审计日志与保留期管理
