# 本项目的 MCP 接口

实现位于 `apps/desktop/mcp_server.py`。这是本项目自己实现的受限业务工具服务，不调用 Tulpa，不安装 Tulpa，也不把它的数据库、启动器、模型或工具系统拷进来。

MCP 与底层 QQ 接入器分别实现。MCP 客户端操作商品任务，任务再按快照和策略决定是否调用用户自己的 QQ 连接器；没有“任意 QQ 号码发送”工具。

## 本机接入

1. 运行本地工作流。
2. 在“QQ 账号管理”下方找到“MCP 客户端连接”。直接发货商品也可以使用 MCP，不需要 QQ 账号。
3. 明确选择允许访问的商品。默认只开放读取；需要创建任务或确认执行时分别勾选写权限。
4. 创建连接，复制通用 MCP 配置。原始令牌仅显示这一次，数据库只保存摘要；丢失后撤销并新建。
5. 在自己的 MCP 客户端配置 Streamable HTTP，地址 `http://127.0.0.1:15179/mcp`，使用 `Authorization: Bearer <界面生成的令牌>`。

通用 JSON 示例（具体字段以客户端支持的配置格式为准）：

```json
{
  "mcpServers": {
    "xianyu-pilot": {
      "url": "http://127.0.0.1:15179/mcp",
      "headers": { "Authorization": "Bearer <界面生成的令牌>" }
    }
  }
}
```

不要把带真实令牌的客户端配置提交到 Git。此版本没有自动修改任何 Agent 的配置。

## 工具范围

| 权限 | 工具 | 限制 |
| --- | --- | --- |
| 读取 | `get_workspace_status`、`list_products`、`list_qq_services`、`list_tasks`、`get_task` | 只读取明确授权商品及其任务，不返回账号凭据 |
| 创建任务 | `create_task` | 必须在授权商品内，订单编号幂等；可能触发该商品既有执行策略 |
| 操作任务 | `update_task_parameters`、`confirm_task`、`cancel_task`、`stop_task_delivery` | 使用当前任务版本，按状态校验；不扩大 QQ 目标或改绑账号 |

没有账号管理、令牌读取、权限修改、任意 QQ 发送、跨账号切换、人工付款确认或人工发货确认工具。卖家人工执行记录只能在本机界面核对后操作。

读取权限包含授权任务的参数和结果，应只交给信任的客户端。写权限是商品级业务授权，不是每次调用弹窗；创建任务或更新参数也可能触发自动策略，所以两种写权限均默认关闭。

## 协议及保护

- Stateless Streamable HTTP，JSON-RPC 2.0，支持 `initialize`、初始化通知、`ping`、`tools/list`、`tools/call`。
- 协商支持 `2025-03-26`、`2025-06-18`、`2025-11-25`。客户端需声明接受 JSON 与事件流；服务当前返回单个 JSON 响应。
- GET 返回 405：当前不提供服务端 SSE 通知。不提供 stdio、旧式 HTTP+SSE、OAuth 发现或公网托管。
- 每次请求验证 Bearer 摘要、有效期与撤销状态；连接最多 30 天有效，每分钟最多 120 次请求。
- 校验本机 Host 和 Origin，不在 URL 放令牌。MCP 不使用界面的 Cookie/CSRF 作为权限依据。
- 工具参数使用严格模型校验；不能传入租户覆盖值或任意发送目标。状态变更需 `expected_revision`，旧版本操作失败而不隐式重试。
- 工具失败返回 `isError`；业务内容不会成为新指令。未实现的真实平台能力不会生成成功平台回执。

MCP 握手、工具发现、权限过滤、订单重放、跨范围拒绝、撤销及 HTTP 请求边界已通过本机集成测试。尚未逐个验证所有第三方 MCP 客户端，也未进行真实 QQ 或闲鱼账号联调。

协议依据：[MCP Streamable HTTP](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)、[MCP Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)。可选 QQ 适配器依据 [OneBot 11 HTTP](https://github.com/botuniverse/onebot-11/blob/master/communication/http.md) 与 [签名事件上报](https://github.com/botuniverse/onebot-11/blob/master/communication/http-post.md)。这些协议资料不是所依赖的可执行程序。
