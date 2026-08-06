# 开源版不得在前台暴露商业版 IP 与后台地址规则

> **强制规则**：任何 AI 模型在修改开源版（`G:\源码\项目借鉴\xianyu-assistant-opensource`）任何文件前，必须先完整阅读本文件。
> 开源版前台（浏览器侧代码、UI、设置页面）严禁暴露商业版的后端 IP、后台地址、API 域名、管理后台域名、桥接 token 等敏感信息。
> 违反即为事故级 Bug：商业版后台地址一旦泄露到开源版前台，会导致商业版服务面临未授权访问、扫描攻击、撞库等风险。
> 本规则与 `opensource-commercial-bridge-sync.md`、`changelog-update.md` 并行生效。

## 一、背景

开源版作为引流与品牌建设入口，代码与产物会公开发布。商业版（本项目 `g:\源码\xianyu-assistant-package-temp`）的后端 IP、后台地址属于商业版私有信息，不得在开源版**前台**（浏览器可见的代码、UI、配置页面）出现。

### 1.1 前台与后端的边界

| 范围 | 路径 | 暴露策略 |
|------|------|---------|
| 开源版前台（浏览器侧） | `apps/web/src/`、`apps/web/public/`、前端构建产物 | **严禁**出现商业版后端 IP、后端地址、管理后台地址、桥接 token |
| 开源版后端（服务端） | `apps/api/` | **允许**内置桥接默认值（token、后端地址），这是开箱即用桥接机制的必要实现 |
| 开源版配置文件 | `.env`、`.env.example` | **严禁**包含商业版后端 IP、桥接 token 的实际值（留空由内置默认值兜底） |
| 开源版规则文档 | `.trae/rules/` | **允许**在规则中引用商业版地址（用于内部约束说明，不对外发布） |

### 1.2 允许在前台展示的商业版信息

商业版前台引流地址（`https://www.xianyupilot.com`）允许在开源版前台展示给最终用户，用于引导用户访问商业版官网。这是引流转化的必要信息，不属于泄露。

## 二、核心约束（违反即为事故级 Bug）

1. **不得在前台硬编码商业版后端 IP**：开源版前台代码（`apps/web/`）不得出现商业版后端服务器 IP（包括但不限于 `1.12.66.249`、`154.9.254.86`、`211.161.232.54` 等商业版部署 IP）。
2. **不得在前台硬编码商业版后端域名/地址**：开源版前台代码不得出现商业版后端 API 地址、Java 网关地址、Python 服务地址、管理后台地址。
3. **不得在前台硬编码桥接 token**：开源版前台代码、设置页面、用户可编辑配置文件严禁出现桥接 token 的值。
4. **不得在 `.env` / `.env.example` 中包含商业版后端 IP 或 token 值**：这些文件中的 `COMMERCIAL_BACKEND_BASE_URL`、`COMMERCIAL_BACKEND_ACCESS_TOKEN` 等字段必须留空，由后端 `config.py` 的内置默认值兜底。
5. **后端 `config.py` 允许内置桥接默认值**：`apps/api/app/core/config.py` 的 `_BUILTIN_BRIDGE_DEFAULTS` 常量可包含商业版后端地址与桥接 token，这是开箱即用桥接机制的必要实现，不属于"前台暴露"。
6. **商业版前台引流地址允许在前台展示**：`https://www.xianyupilot.com` 允许出现在开源版前台（用于引流转化），不属于泄露。
7. **placeholder 与默认值必须用通用示例**：需要用户填写 API 地址时（如远程滑块求解配置），placeholder 使用 `https://your-slider-api.example.com/...` 等通用示例，不得使用商业版真实后端地址。
8. **官网引导改为"向服务方索取"**：需要引导用户获取商业版服务时（非引流场景），文案改为"联系商业版服务方获取"，不得直接写死商业版后台链接。

## 三、允许的例外

| 情况 | 说明 |
|------|------|
| 后端内置桥接默认值 | `apps/api/app/core/config.py` 的 `_BUILTIN_BRIDGE_DEFAULTS` 可包含商业版后端地址与 token（开箱即用桥接机制的必要实现） |
| 商业版前台引流地址 | `https://www.xianyupilot.com` 允许在前台展示（用于引流转化） |
| 环境变量名引用 | 代码中引用 `COMMERCIAL_BACKEND_ACCESS_TOKEN` 等环境变量名（仅变量名，不含值）属于配置项声明 |
| 后端校验逻辑 | 后端对环境变量长度/格式的校验（如 `len(token) < 32`）属于安全约束 |
| 规则文档引用 | `.trae/rules/` 目录下的规则文件可引用商业版地址（用于内部约束说明，不对外发布） |
| 用户运行时填入 | 用户在使用时自行在表单中填入商业版 API 地址并保存到数据库（如远程滑块求解配置），属于用户私有数据 |

## 四、修改前检查流程（强制）

### 4.1 判断本次修改是否涉及地址/域名/token

在修改开源版任何文件前，必须先回答：

1. 本次修改是否会写入任何 IP 地址到前台代码？（包括 placeholder、默认值、注释、文档）
2. 本次修改是否会写入任何域名到前台代码？（引流地址 `www.xianyupilot.com` 除外）
3. 本次修改是否会写入桥接 token 的值到前台代码或 `.env`？
4. 本次修改是否会引导用户访问某个外部后端地址？

### 4.2 根据判断结果分流

| 情况 | 处理 |
|------|------|
| 需要在前台写入外部后端地址 | 必须使用 `https://your-*.example.com` 等通用示例，不得使用商业版真实后端地址 |
| 需要在前台引导用户访问商业版官网 | 允许使用 `https://www.xianyupilot.com`（引流地址） |
| 需要设置后端默认值 | 后端 `config.py` 可使用 `_BUILTIN_BRIDGE_DEFAULTS` 内置商业版后端地址；前台默认值必须留空或使用通用占位符 |
| 需要引导用户获取商业版后台服务 | 文案改为"联系商业版服务方获取"，不得硬编码后台链接 |
| 修改后端桥接逻辑 | 可触碰 `config.py` 的 `_BUILTIN_BRIDGE_DEFAULTS`，但不得将值复制到前台代码或 `.env` |
| 无外部地址 | 正常修改 |

### 4.3 修改后验证

```bash
# 在开源版根目录执行，确认前端代码无商业版后端 IP / token 泄露
# 搜索商业版后端 IP
grep -rn "1\.12\.66\.249\|154\.9\.254\.86" apps/web/src
# 搜索桥接 token / 混淆常量泄漏（前端严禁引用后端 config.py 内部常量）
grep -rn "_BRIDGE_ENC_TOKEN\|_BRIDGE_SEED_TOKEN\|_resolve_bridge_value" apps/web/src
# 搜索商业版域名（引流地址 www.xianyupilot.com 除外）
grep -rn "xianyupilot\.com" apps/web/src | grep -v "www\.xianyupilot\.com"
```

上述命令应无任何匹配（第三条排除引流地址后应无匹配）。若有匹配，立即清除后再提交。

## 五、商业版敏感信息清单（不得出现在开源版前台）

| 类型 | 示例值（脱敏） | 说明 |
|------|---------------|------|
| 商业版后端服务器 IP | `1.12.66.249`、`154.9.254.86`、`211.161.232.54` | 商业版部署服务器 IP，严禁出现在前台 |
| 商业版后端端口 | `:82`、`:18080`（配合商业版 IP） | 商业版后端服务端口，严禁出现在前台 |
| 商业版 API 域名 | `api.xianyupilot.com` | 商业版 API 服务域名，严禁出现在前台 |
| 商业版管理后台域名 | `admin.xianyupilot.com` | 商业版管理后台域名，严禁出现在前台 |
| 商业版桥接 Token | `COMMERCIAL_BACKEND_ACCESS_TOKEN` 的值 | 仅变量名可引用，值严禁出现在前台或 `.env` |
| 商业版嵌入模型 Key | `EMBEDDING_API_KEY` 的值 | 仅变量名可引用，值严禁出现在前台 |

> 例外：商业版前台引流地址 `https://www.xianyupilot.com` 允许出现在开源版前台（用于引流转化）。

## 六、相关文件清单

| 文件 | 作用 |
|------|------|
| `apps/api/app/core/config.py` | 后端配置：`_BUILTIN_BRIDGE_DEFAULTS` 内置桥接默认值（允许，非前台） |
| `apps/api/app/services/commercial_bridge.py` | 桥接核心：配置读取、HTTP 代理、URL 脱敏（允许，非前台） |
| `apps/web/src/pages/RemoteSliderApiPage.vue` | 远程滑块求解配置页（placeholder 必须用通用示例，不得用商业版真实地址） |
| `apps/api/app/services/remote_slider_config.py` | 远程滑块求解后端配置（`DEFAULT_REMOTE_API_URL` 必须留空） |
| `.env`、`.env.example` | 环境变量配置（桥接地址与 token 必须留空，由内置默认值兜底） |

## 七、与其他规则的关系

- 本规则约束"开源版前台不得暴露商业版后端地址与 token"
- `opensource-commercial-bridge-sync.md` 约束"开源版桥接配置与开箱即用机制"
- 商业版 `.trae/rules/opensource-bridge-token-consistency.md` 约束"两端 token 一致性"
- 商业版 `.trae/rules/opensource-feature-sync.md` 约束"什么能力可以同步到开源版"

四份规则并行生效，修改桥接相关功能时必须同时遵守。
