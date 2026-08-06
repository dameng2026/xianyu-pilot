# 开源版与商业版桥接同步规则

> **强制规则**：任何 AI 模型在修改开源版"广告显示、反馈建议、广告申请、广告套餐、支付订单"相关功能，或修改桥接 token、商业版后端地址等敏感数据前，必须先完整阅读本文件。
> 开源版必须与商业版正确桥接，否则上述功能全部不可用（fail-closed 策略）。
> 桥接相关敏感数据必须代码混淆处理，严禁以明文存储在源码中。
> 本规则与 `changelog-update.md`、`docker-image-publish.md`、`opensource-no-commercial-exposure.md` 并行生效，并与商业版 `.trae/rules/opensource-bridge-token-consistency.md` 对应。

## 一、背景与功能概述

开源版项目的广告、反馈、广告申请等功能并非独立实现，而是通过商业版桥接（commercial bridge）调用线上商业版后端 API。桥接采用 **fail-closed（失败即关闭）** 策略：商业版后端不可达或返回错误时，直接返回 503/400 或降级到本地兜底，绝不展示未确认付费的内容。

### 1.1 开箱即用原则（核心目标）

开源版用户从 Git 下载源码、自行部署后，**无需配置任何商业版 token 或后端地址**，即可直接连接商业版后端，使用全部桥接功能。

实现方式：
- 桥接 token、商业版后端地址、前台引流地址均作为**内置默认值**存储在 `apps/api/app/core/config.py` 的 `_BUILTIN_BRIDGE_DEFAULTS` 常量中
- 上述敏感值在源码中以**混淆编码**存储（`base64(XOR(plaintext, sha256(seed)))`），运行时由 `_resolve_bridge_value()` 解码，不以明文出现在源码中
- 三能力开关（`mutation_idempotency` / `payment_idempotency` / `paid_ad_placement`）默认全部开启
- 环境变量与 secrets 文件可覆盖内置默认值（用于 token 轮换等场景），但用户无需手动配置

涉及的功能：

| 功能 | 桥接策略 | 默认状态 |
|------|---------|---------|
| 广告显示（轮播图 / 文字广告） | 强依赖桥接，fail-closed | 可用（内置默认） |
| 广告套餐查询 | 基础依赖桥接 | 可用（内置默认） |
| 广告申请提交 | 强依赖桥接（三开关全开） | 可用（内置默认） |
| 广告支付订单 | 强依赖桥接（三开关全开） | 可用（内置默认） |
| 反馈建议 | 桥接优先 + 本地兜底 | 可用（桥接或本地模式） |

## 二、核心约束（违反即为事故级 Bug）

1. **两端 token 必须完全一致**：开源版 `_BUILTIN_BRIDGE_DEFAULTS["commercial_backend_access_token"]`（解码后值）必须与商业版 `.env.production` 的 `OPEN_SOURCE_BRIDGE_TOKEN` 完全相同，否则桥接返回 401。
2. **token 长度必须 ≥ 32 字符**：开源版 `config.py` 生产环境校验要求。
3. **开源版必须开箱即用**：用户下载部署后，不得要求用户手动配置 token、后端地址或能力开关即可使用全部桥接功能。内置默认值是开箱即用的核心机制，不得移除。
4. **敏感数据必须代码混淆**：开源版 `config.py` 中的桥接 token、商业版后端地址、前台引流地址等敏感值**严禁以明文存储**，必须使用 `base64(XOR(plaintext, sha256(seed)))` 混淆编码存储，运行时由 `_resolve_bridge_value()` 解码。每次修改或轮换这些敏感值时，必须重新生成混淆编码（使用 `_regenerate_bridge_encoding()` 函数），严禁将明文值直接写入源码。
5. **不得在前台暴露桥接 token**：开源版前端代码（`apps/web/`）严禁出现桥接 token 的值；开源版设置页面严禁提供修改 token 的入口；`.env` 与 `.env.example` 严禁包含 token 值。
6. **三个能力开关默认全部开启**：`COMMERCIAL_BACKEND_MUTATION_IDEMPOTENCY_ENABLED`、`COMMERCIAL_BACKEND_PAYMENT_IDEMPOTENCY_ENABLED`、`COMMERCIAL_BACKEND_PAID_AD_PLACEMENT_ENFORCED` 在 `config.py` 中默认值为 `True`。仅在商业版后端尚未通过对应合约验证时才可关闭。
7. **不得移除 fail-closed 策略**：广告相关功能在商业版后端不可达或返回错误时必须降级，不得展示未确认付费的内容。
8. **不得移除反馈功能的本地兜底**：反馈建议在桥接调用失败时必须降级到本地存储，不得直接报错。
9. **生产环境强制 HTTPS（用户自配地址时）**：若用户自行配置 `COMMERCIAL_BACKEND_BASE_URL`（覆盖内置默认），生产环境必须为 HTTPS。内置默认地址因在 `validate_security_defaults` 之后由 `_apply_builtin_bridge_defaults` 注入，绕过 HTTPS 校验，这是开箱即用的必要设计。
10. **token 轮换必须双向同步**：轮换 token 时必须同时更新开源版 `config.py` 的混淆编码（`_BRIDGE_ENC_*` 与 `_BRIDGE_SEED_*`）与商业版线上 `.env.production` 的 `OPEN_SOURCE_BRIDGE_TOKEN`。

## 三、当前权威配置

> **注意**：以下为当前生效配置。若需轮换 token，必须同步更新本文件、开源版 `config.py` 混淆编码、商业版线上 `.env.production` 三处。详见商业版 `.trae/rules/opensource-bridge-token-consistency.md`。

### 3.1 桥接 Token

```
OPEN_SOURCE_BRIDGE_TOKEN = jlWgrNxw_lHnJJs0QkU6hNAHrekPZt-WUvCHWFPo5MdQOmnkWaqWMOEfVHfiDCTX
```

> **注意**：开源版 `config.py` 中**不存储明文 token**，仅存储混淆编码值（`_BRIDGE_ENC_TOKEN` 常量）与解码种子（`_BRIDGE_SEED_TOKEN`），运行时由 `_resolve_bridge_value()` 解码。上方的明文值仅供规则文档记录与一致性校验使用。

| 配置项 | 开源版存储方式 | 当前编码值 |
|--------|--------------|-----------|
| token | `_BRIDGE_ENC_TOKEN`（混淆编码） | `0U34kM8JkBvWvFRjcJp3cCsYy3XY+0TCFYEu4L9fAa/uV+y/6gG4A7ydeFx1vWorLRLvFP36QOwxrCPZoWh4oA==` |
| 解码种子 | `_BRIDGE_SEED_TOKEN` | `xianyu.bridge.token.3a1c9786` |

### 3.2 商业版后端地址

```
COMMERCIAL_BACKEND_BASE_URL = http://211.161.232.54:18080
```

> **注意**：开源版 `config.py` 中**不存储明文地址**，仅存储混淆编码值。

| 配置项 | 开源版存储方式 | 当前编码值 |
|--------|--------------|-----------|
| 后端地址 | `_BRIDGE_ENC_BACKEND`（混淆编码） | `noGcALZ+NUyrnGCFtSMSZ3WsvzSbI7znFXRP` |
| 解码种子 | `_BRIDGE_SEED_BACKEND` | `xianyu.backend.url.e1c1b20b` |

### 3.3 商业版前台引流地址（可展示给用户）

```
COMMERCIAL_FRONTEND_URL = https://www.xianyupilot.com
```

此地址用于在开源版"关于我们"等页面引导用户访问商业版官网，允许展示给最终用户。源码中同样以混淆编码存储以统一处理。

| 配置项 | 开源版存储方式 | 当前编码值 |
|--------|--------------|-----------|
| 前台地址 | `_BRIDGE_ENC_FRONTEND`（混淆编码） | `zVB6D3X4NZfv2QYII2yZz0TaNJanh3S97d8j` |
| 解码种子 | `_BRIDGE_SEED_FRONTEND` | `xianyu.frontend.url.e1896305` |

## 四、桥接配置加载机制（不得更改）

### 4.1 配置加载顺序

1. `Settings()` 初始化时，从环境变量 / `.env` / secrets 文件加载 `commercial_backend_*` 字段
2. `validate_security_defaults` 校验配置（空值跳过校验）
3. `_apply_builtin_bridge_defaults(settings)` 在校验后注入内置默认值（仅填充空字段）
4. 内置默认值由 `_resolve_bridge_value()` 运行时解码混淆编码得到

### 4.2 覆盖优先级

```
环境变量 / .env / secrets 文件  >  _BUILTIN_BRIDGE_DEFAULTS（混淆编码解码值）
```

- 用户未配置时：使用混淆编码解码后的内置默认值（开箱即用）
- 用户显式配置时：用户配置覆盖内置默认值（用于 token 轮换等场景）
- secrets 文件为空时：回退到内置默认值

### 4.3 混淆解码机制

```python
# 混淆算法：base64(XOR(plaintext, sha256(seed)))
# 每个敏感值使用独立的 seed，seed 经 SHA256 派生 32 字节密钥
# 密钥不直接出现在源码中，仅 seed 出现在源码中

def _resolve_bridge_value(encoded: str, seed: str) -> str:
    """运行时解码内置桥接默认值。"""
    raw = base64.b64decode(encoded.encode("ascii"))
    key = hashlib.sha256(seed.encode("utf-8")).digest()
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(raw)).decode("utf-8")
```

### 4.4 关键文件

| 文件 | 作用 |
|------|------|
| `apps/api/app/core/config.py` | `_BUILTIN_BRIDGE_DEFAULTS` 常量、`_resolve_bridge_value()` 解码函数、`_BRIDGE_ENC_*` / `_BRIDGE_SEED_*` 混淆编码常量、`_regenerate_bridge_encoding()` 轮换函数、三能力开关默认值 |
| `apps/api/app/services/commercial_bridge.py` | 桥接核心：配置读取、能力门控、HTTP 代理、健康探测 |
| `apps/api/app/services/ad_payment_order_attempt.py` | 支付订单本地幂等状态机 |
| `apps/api/app/api/v1/routes/home_content.py` | `/carousel/list`、`/announcement/list` 路由（fail-closed） |
| `apps/api/app/api/v1/routes/frontend_compat.py` | `/ads/*`、`/feedback/*` 路由（广告 fail-closed，反馈本地兜底） |

## 五、修改桥接功能前的检查流程（强制）

### 5.1 确认混淆编码未被破坏

```bash
# 确认 _BRIDGE_ENC_* 和 _BRIDGE_SEED_* 常量存在且非空
grep -A 1 "_BRIDGE_ENC_TOKEN\|_BRIDGE_ENC_BACKEND\|_BRIDGE_ENC_FRONTEND" apps/api/app/core/config.py
grep "_BRIDGE_SEED_" apps/api/app/core/config.py

# 确认 _resolve_bridge_value 函数存在
grep -n "_resolve_bridge_value" apps/api/app/core/config.py

# 确认 _regenerate_bridge_encoding 函数存在
grep -n "_regenerate_bridge_encoding" apps/api/app/core/config.py

# 确认三能力开关默认值是否为 True
grep "commercial_backend_.*_enabled: bool" apps/api/app/core/config.py
```

确认：
- `_BRIDGE_ENC_TOKEN`、`_BRIDGE_ENC_BACKEND`、`_BRIDGE_ENC_FRONTEND` 三个常量存在且非空
- `_BRIDGE_SEED_TOKEN`、`_BRIDGE_SEED_BACKEND`、`_BRIDGE_SEED_FRONTEND` 三个种子常量存在
- `_resolve_bridge_value()` 函数存在且被 `_BUILTIN_BRIDGE_DEFAULTS` 调用
- `_regenerate_bridge_encoding()` 函数存在（供 token 轮换使用）
- 三个能力开关默认值均为 `True`
- `settings = Settings()` 后调用了 `_apply_builtin_bridge_defaults(settings)`

### 5.2 确认源码无明文敏感值

```bash
# 搜索 config.py 中的明文 token
grep -n "jlWgrNxw_lHnJJs0QkU6hNAHrekPZt" apps/api/app/core/config.py
# 搜索 config.py 中的明文后端 IP
grep -n "154\.9\.254\.86" apps/api/app/core/config.py
# 搜索 config.py 中的明文前台 URL
grep -n "https://www.xianyupilot.com" apps/api/app/core/config.py
```

上述命令应无任何匹配。若有匹配，说明混淆编码被破坏，需立即用 `_regenerate_bridge_encoding()` 重新生成编码值。

### 5.3 确认前台无 token / 后端 IP 泄露

```bash
# 搜索前端代码中的商业版后端 IP
grep -rn "154\.9\.254\.86\|1\.12\.66\.249" apps/web/src
# 搜索前端代码中的桥接 token
grep -rn "jlWgrNxw_lHnJJs0QkU6hNAHrekPZt" apps/web/src
```

上述命令应无任何匹配。

> 例外：商业版前台引流地址 `https://www.xianyupilot.com` 允许出现在前端（用于引流），不属于泄露。

### 5.4 验证桥接连通性（可选）

```bash
# 解码内置 token 并调用桥接 health 端点
TOKEN=$(python -c "
import base64, hashlib
raw = base64.b64decode('0U34kM8JkBvWvFRjcJp3cCsYy3XY+0TCFYEu4L9fAa/uV+y/6gG4A7ydeFx1vWorLRLvFP36QOwxrCPZoWh4oA=='.encode('ascii'))
key = hashlib.sha256('xianyu.bridge.token.3a1c9786'.encode('utf-8')).digest()
print(bytes(b ^ key[i % len(key)] for i, b in enumerate(raw)).decode('utf-8'))
")
curl -s -H "X-Open-Source-Token: $TOKEN" http://211.161.232.54:18080/admin-api/open-source-bridge/health
```

应返回 200 状态码。

## 六、token 轮换流程（如需更新）

1. 生成新 token（≥ 32 字符，URL 安全字符）：
   ```powershell
   -join ((48..57) + (65..90) + (97..122) + (95, 45) | Get-Random -Count 48 | ForEach-Object { [char]$_ })
   ```

2. **生成混淆编码值**（严禁将明文 token 写入源码）：
   ```bash
   # 在开源版 apps/api 目录执行
   python -c "
   import sys; sys.path.insert(0, '.')
   from app.core.config import _regenerate_bridge_encoding
   result = _regenerate_bridge_encoding(
       token='<新token>',
       backend_url='http://211.161.232.54:18080',
       frontend_url='https://www.xianyupilot.com',
   )
   for k, v in result.items():
       print(f'{k} = \"{v}\"')
   "
   ```
   将输出的 6 个值更新到 `config.py` 的 `_BRIDGE_ENC_*` 和 `_BRIDGE_SEED_*` 常量。

3. 同步更新四处：
   - 开源版 `apps/api/app/core/config.py` 的 `_BRIDGE_ENC_*` 和 `_BRIDGE_SEED_*` 常量（**混淆编码，非明文**）
   - 商业版线上 `.env.production` 的 `OPEN_SOURCE_BRIDGE_TOKEN`（明文，由服务器环境变量保护）
   - 本规则文件第三节"当前权威配置"
   - 商业版 `.trae/rules/opensource-bridge-token-consistency.md` 第三节

4. 重启两端服务使配置生效。

5. 验证：带新 token 调用桥接 health 端点返回 200。

## 七、代码混淆处理要求（强制）

> **强制规则**：任何 AI 模型或开发者在修改开源版桥接 token、商业版后端地址、前台引流地址等敏感数据时，**必须使用代码混淆处理**，严禁将明文值直接写入源码。

### 7.1 混淆算法

```
编码：encoded = base64( XOR( plaintext_utf8, sha256(seed) ) )
解码：plaintext = XOR( base64_decode(encoded), sha256(seed) ).decode('utf-8')
```

- 每个敏感值使用独立的 seed（如 `xianyu.bridge.token.v1`、`xianyu.backend.url.v1`）
- seed 经 SHA256 派生 32 字节密钥，密钥不直接出现在源码中
- XOR 结果经 Base64 编码为 ASCII 字符串存储
- 此为代码混淆（obfuscation），非密码学加密——仅防止随意查看、grep 搜索、IDE 全局搜索时直接看到敏感值

### 7.2 混淆实现位置

| 文件 | 函数/常量 | 作用 |
|------|----------|------|
| `apps/api/app/core/config.py` | `_resolve_bridge_value(encoded, seed)` | 运行时解码 |
| `apps/api/app/core/config.py` | `_BRIDGE_ENC_TOKEN` / `_BRIDGE_ENC_BACKEND` / `_BRIDGE_ENC_FRONTEND` | 混淆编码值常量 |
| `apps/api/app/core/config.py` | `_BRIDGE_SEED_TOKEN` / `_BRIDGE_SEED_BACKEND` / `_BRIDGE_SEED_FRONTEND` | 解码种子常量 |
| `apps/api/app/core/config.py` | `_regenerate_bridge_encoding(token, backend_url, frontend_url)` | 生成新编码值（用于轮换） |

### 7.3 何时必须重新生成混淆编码

| 场景 | 是否需重新生成编码 |
|------|-------------------|
| token 轮换 | ✅ 是，必须用 `_regenerate_bridge_encoding()` 生成新编码 |
| 商业版后端地址变更 | ✅ 是 |
| 前台引流地址变更 | ✅ 是 |
| 修改桥接逻辑但不改敏感值 | ❌ 否，保持现有编码 |
| 仅修改前端 UI | ❌ 否 |

### 7.4 混淆验证清单

每次修改开源版桥接相关代码后，必须执行以下验证：

1. **语法校验**：`python -c "import ast; ast.parse(open('apps/api/app/core/config.py').read()); print('OK')"`
2. **解码一致性**：解码后的值必须与权威配置一致
3. **源码无明文**：`grep` 搜索明文 token / IP / URL 应无匹配
4. **功能可用**：开源版启动后桥接 health 端点返回 200

## 八、与其他规则的关系

- 本规则约束"开源版侧的桥接配置、开箱即用、代码混淆处理"
- 商业版 `.trae/rules/opensource-bridge-token-consistency.md` 约束"两端 token 一致性与上线检查"
- `opensource-no-commercial-exposure.md` 约束"开源版不得在前台暴露商业版地址"
- 三份规则并行生效，修改桥接相关功能时必须同时遵守。
