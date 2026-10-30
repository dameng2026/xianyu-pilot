# 官方订单核验、双监听与统一组合发货

日期：2026-10-29。基于阶段 1 的原订单、库存和逐段回执增量实现，不引入外部聊天机器人，不另建订单或卡密库。

## 当前交付与真实接入边界

本轮提供：签名卖家订单 HTTP 适配器、严格状态解析、持久 IM 收件箱、即时唤醒及 20 秒轮询、付款与关键词核验、统一 SKU 规则、真实 IM ACK/图片上传/平台确认的网关适配、宿主启停组合入口，以及无网络故障回归。

**普通桌面入口仍默认不联网、不自动发货。** 本次没有使用真实账号或真实订单验证平台返回结构。公开官方来源未能核实 `mtop.taobao.idle.seller.order.list` 的当前请求/响应契约；仓库原实现使用的是 `mtop.taobao.idle.trade.merchant.sold.get`。因此不能把离线用例视为新接口已经真实可用。

`SellerOrdersAPI` 默认 `contract_verified=False`，在发起网络请求前阻断。两个订单接口可显式选择，不会在失败后悄悄切换。启用前须以授权账号的脱敏返回样例验证参数、分页、状态字段、SKU、数量、退款标记和会话归属。缺字段时修订显式适配器并回归，不能增加“未知即已付款”的兜底。

## 四道核验及执行

1. IM 消息只提供订单线索；轮询列表也只提供候选。针对候选通过已登录卖家会话重新查询订单，要求状态**精确等于** `WAIT_SELLER_SEND_GOODS`，且没有退款标记。`WAIT_BUYER_PAY`、`TRADE_CLOSED`、中文展示文案、未知状态及缺失字段均不出库。
2. 核验卖家、账号、买家、商品、SKU、数量、金额与本地绑定。付款模式直接允许排队；付款加关键词模式必须存在来自该买家 IM 的完整触发词，并且唯一对应一笔待发货订单。`不要发卡` 不匹配 `发卡`；同买家同商品多笔订单时不猜测归属。
3. SQLite 以 `(account_id, external_order_id)` 和首发任务唯一索引去重。已经完成、在途和需要核对的任务不重新建单或领卡。实时与轮询共用同一入口。网络请求不持数据库事务。
4. 继承阶段 1 的整单原子占用：N 件须一次取得 N 条不同卡密。缺货保留 stock_blocked 任务、错误说明与本地告警状态，零卡密占用、零消息发送。

核验事实写入原订单表，携带 platform 来源、官方状态、卖家与核验时间；新任务还须具有匹配当前规则版本的触发授权。事实超过 30 秒、时钟回拨、账号停用、身份或金额/数量变化，底层库存领取与步骤领取会阻断。

每次外部发送前都重新查询官方订单；收到支付消息并不意味着后续步骤永远有资格执行。消息已经交付后平台确认超时，保留独立 unknown。后续官方查询证实已发货，且全部消息段都已确认、订单身份完全匹配，才完成对账；从不重发卡密。消息段本身 unknown 始终保留人工核对。

真实平台确认不能仅凭人工备注完成；底层核对接口要求数据库中已有 30 秒内、身份与数量金额一致的官方已发货事实。订单接口返回的新 Cookie 交给本机凭据回调保存；即便需要刷新 Cookie，也不会自动重放平台确认请求。

## 双监听与故障处理

- 复用原 IM WebSocket 的注册、同步、ACK、心跳和断线重连。桌面客户端用注入的本地凭据回调替代 MySQL 写回，关闭旧通知机器人和自动爬虫解验证码路径；登录失效等待用户重新授权。
- `on_ws_message` 先写 `delivery_inbox` 再唤醒消费者，不等待下一次定时探针。消息编号按账号唯一，编号相同而内容改变报冲突。网络核验失败不把消息标为已完成，下一轮可恢复。
- 默认每 20 秒探测，允许配置 15–30 秒。分页必须明确结束、不得重复订单，最多 100 页；接口失败则暂停并退避至最多 300 秒，不把失败当作空订单列表。
- 同账号实时消费者与轮询串行，防止彼此竞争；不同账号分别调度。待继续的原任务先按冻结快照处理，不因规则编辑而换内容。
- 关键词使用记录不可改写，不能通过新关键词覆盖旧记录、再把旧消息授权给另一订单；新建任务的授权有效期为 30 分钟，规则版本改变后不沿用旧授权。
- `GET /api/desktop/monitor-status` 提供最近监控事件、缺货任务数和待核对数；发货记录保留每段状态和审计。告警由本地 UI 消费，不发送给外部机器人。完整界面仍待挂载。

## 配置唯一来源

`delivery_rule` 是唯一权威配置，新增 `sku_id/config_version/plan_json/trigger_json`。`delivery_goods_config.config_json` 仅为同一事务维护的投影，执行器和查询均读取权威规则。禁止直接修改投影作为另一套配置。

- `GET/POST/PUT/DELETE /api/auto-delivery/rules`：旧规则入口继续可用，写入经过统一服务。
- `GET/PUT /api/auto-delivery/goods/{goodsId}/config`：规范的商品 SKU 组合配置入口。
- 同一商品每个 SKU 只允许一条有效规则；无精确 SKU 规则时阻断，不回退通用规格。
- 商品配置可携带 `expectedVersion`，过期写入返回 409。修改、移除和旧规则入口写入均原子更新 JSON 投影。

规范 PUT 示例（先 GET 获取当前 version；示例中的图片须已存在于程序目录的 data/assets/）：

```json
{
  "expectedVersion": 0,
  "variants": [{
    "skuId": "sku-a",
    "enabled": true,
    "trigger": {"mode": "payment_keyword", "keywords": ["发卡", "提卡"]},
    "plan": {
      "skuId": "sku-a",
      "cardGroupId": 1,
      "confirmShipment": true,
      "maxAttempts": 3,
      "lowStockThreshold": 5,
      "pauseOnLowStock": false,
      "parts": [
        {"kind": "text", "content": "感谢购买，您的激活信息如下："},
        {"kind": "card", "content": "卡号：{卡号}\n密码：{密码}"},
        {"kind": "text", "content": "使用说明与注意事项……", "repeat": "order"},
        {"kind": "image", "content": "assets/activation-guide.png", "repeat": "order"},
        {"kind": "link", "content": "https://example.invalid/download 提取码：abcd", "repeat": "order"}
      ]
    }
  }]
}
```

卡密段固定按件展开，每件一条；其他段默认按单一次，可设置 `repeat=item`。卡号、密码和完整卡密分别支持 `{卡号}`、`{密码}`、`{卡密}`，不会只发密码漏掉卡号。005 版本起，新任务图片复制到 data/assets/.snapshots 并保存相对引用与 SHA256，原图编辑不影响处理中订单，冻结副本损坏则阻断；更早快照保留原哈希约束。移动整个程序目录不需改引用。多商品明细订单及一件消耗多个卡密的套餐仍未开放。恢复及人工 B 补偿见 [资产安全说明](delivery-safety-recovery.md)。

旧商品 JSON 的付款时机直接内容可迁入同一规范：`header/cardTemplate/content/footer/segments/cardGroupId` 转成有序步骤，`retryCount` 转成总尝试数（含首次），`alertThreshold` 转成低库存阈值，`autoDisableOnLowStock` 转成阈值暂停策略。未知结果始终不重试；已知未发送也不得超过尝试上限。库存阈值只暂停新分配，不改写已冻结任务。既有规则的每日发货上限使用同一事务的日额度预留校验。

仅接受明确的布尔或 0/1 开关，字符串 `false` 不会被当作启用。付款核验不可通过旧 `triggerOnPay=0` 关闭。原货源库 sourceId、收货/评价时机等未迁移能力明确拒绝，不能静默漏发。GET 返回规范 variants；原 Vue 配置编辑器的完整组合 UI 与素材导入 UI 尚未接入，本轮不宣称旧页面已完成端到端适配。

## 宿主接入

入口 `compose_official_runtime` 需要调用方提供：

1. 当前程序 data/app.sqlite3 路径与已绑定的账号列表。
2. 已验证账号归属的 `SellerSession` 提供器，Cookie 的 unb 必须与 seller_id 一致。
3. 异步本机凭据保险库回调，刷新凭据只能加密保存在 data/profiles 或 app.sqlite3；本轮不新增明文凭据文件或上传凭据接口。
4. 通过 IM 官方会话列表/查询验证买家和商品归属的会话解析器，不能从买家文字猜 CID。
5. 经真实脱敏契约验证后才可设置 `contract_verified=True`。

构造后的 runtime 通过 `create_desktop_app(..., platform_runtime=runtime)` 纳入单实例、迁移、启动恢复及退出生命周期。普通 `app.main` 未配置 runtime，因此默认 `automaticDeliveryEnabled=false`；不存在通过普通 HTTP 请求伪造官方事实或一键跳过契约验证的入口。

发送网关复用原 IM 的正面 ACK，只有 code=200 且有消息标识、无 unknown 标记才记确认。图片先校验本地哈希，再由内存上传适配器上传；上传完成不等于买家收到。平台确认使用独立操作和原无需物流接口；请求超时不自动重发，先查官方状态。

## 验证

无网络测试在 `apps/api/tests/test_official_delivery.py`，覆盖：精确状态、退款、伪付款、假订单卡片、关键词归属/否定词/多单歧义、双触发、N 件库存、SKU、缺货恢复、旧新配置统一、过期版本、过期授权、独立确认对账、未知卡密锁定、IM 重连、即刻唤醒、签名请求构造、分页/重定向/畸形响应、账号错配及本地凭据回调。

完整后端与打包门禁：

```powershell
python -m pytest apps/api/tests -q --junitxml=reports/official-delivery.xml
python -m unittest discover -s tests/ci -v
```

这些是适配器及业务逻辑证据，不替代真实账号、接口契约、实际消息收发和 Windows 便携 EXE 的验收。便携数据根和后续托盘要求继续遵守 `desktop-delivery-design.md`。
