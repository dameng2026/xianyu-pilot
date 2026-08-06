import base64
import hashlib
from pathlib import Path
from typing import Any, List
from urllib.parse import urlparse

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

from app.core.file_secrets import resolve_file_secret_values

API_DIR = Path(__file__).resolve().parents[2]
MONOREPO_LAYOUT = API_DIR.parent.name.casefold() == "apps"
ROOT_DIR = API_DIR.parent.parent if MONOREPO_LAYOUT else API_DIR
ROOT_ENV_FILE = ROOT_DIR / ".env"
DEFAULT_UPLOADS_DIR = (API_DIR.parent / "uploads" if MONOREPO_LAYOUT else API_DIR / "uploads").resolve()
FILE_BACKED_SECRET_FIELDS = (
    "mysql_password",
    "mysql_app_password",
    "mysql_migration_password",
    "internal_api_token",
    "commercial_backend_access_token",
    "jwt_secret",
    "cookie_crypto_secret",
    "admin_password_hash",
    "embedding_api_key",
    "redis_password",
    "ai_provider_api_key",
)
OPTIONAL_FILE_BACKED_SECRET_FIELDS = (
    "commercial_backend_access_token",
    "embedding_api_key",
    "ai_provider_api_key",
)


class Settings(BaseSettings):
    app_name: str = "xianyu-assistant-python"
    server_host: str = "127.0.0.1"
    # Isolated local default. Production Compose explicitly overrides this
    # with its private, unpublished container port (12401).
    server_port: int = 15177

    db_path: str = "../dbdata/xianyu_assistant.db"  # kept for backward compat
    uploads_dir: str = str(DEFAULT_UPLOADS_DIR)

    # MySQL 配置（替换 SQLite）
    mysql_host: str = "localhost"
    mysql_port: int = 3306
    mysql_user: str = "xianyu"
    mysql_password: str = Field(default="xianyu_pass", exclude=True, repr=False)
    mysql_password_file: str = Field(default="", exclude=True, repr=False)
    # Role-specific production credentials. Empty values preserve the legacy
    # MYSQL_USER/MYSQL_PASSWORD contract for development and older deployments.
    mysql_app_user: str = ""
    mysql_app_password: str = Field(default="", exclude=True, repr=False)
    mysql_app_password_file: str = Field(default="", exclude=True, repr=False)
    mysql_migration_user: str = ""
    mysql_migration_password: str = Field(default="", exclude=True, repr=False)
    mysql_migration_password_file: str = Field(default="", exclude=True, repr=False)
    mysql_database: str = "xianyu_assistant_admin"
    mysql_connect_timeout_seconds: int = 10
    mysql_read_timeout_seconds: int = 30
    mysql_write_timeout_seconds: int = 30
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout_seconds: int = 30
    db_pool_recycle_seconds: int = 1800

    # Reliable scheduled-task worker. The lease must outlive the execution
    # timeout so another replica cannot claim a still-running task.
    scheduler_poll_interval_seconds: int = 15
    scheduler_task_timeout_seconds: int = 300
    scheduler_lease_seconds: int = 360
    scheduler_batch_size: int = 20

    # 自动发货补发兜底循环：扫描已开启自动发货但未发出的订单，
    # 复用 RealtimeDeliveryCoordinator 的幂等状态机安全补发。
    delivery_recovery_enabled: bool = True
    delivery_recovery_interval_seconds: int = 600  # 10 分钟
    delivery_recovery_batch_size: int = 50
    # 订单付款后至少等待该秒数才允许补发，避免与 WS 实时事件抢资源。
    delivery_recovery_min_age_seconds: int = 300

    # 内部调用令牌。Phase 1 起内部接口 fail-closed：为空时拒绝内部调用。
    internal_api_token: str = Field(
        default="dev-only-internal-api-token-change-me-32-chars",
        exclude=True,
        repr=False,
    )
    internal_api_token_file: str = Field(default="", exclude=True, repr=False)
    app_env: str = "dev"

    # Comma-separated browser origins allowed to call the service directly.
    cors_allowed_origins: str = ""

    # 商业后台反馈同步配置
    # 桥接地址、访问令牌与前台引流地址均内置默认值（见 _BUILTIN_BRIDGE_DEFAULTS），
    # 开源版用户下载部署后无需手动配置 token 即可直连商业版后端，使用广告申请、
    # 反馈提交、动态获取广告等能力。环境变量可覆盖内置默认值。
    commercial_backend_base_url: str = ""
    commercial_backend_bridge_prefix: str = "/admin-api/open-source-bridge"
    commercial_backend_health_path: str = "/admin-api/health"  # backward compat alias
    commercial_backend_admin_health_path: str = "/admin-api/health"
    commercial_backend_user_health_path: str = "/api/health"
    commercial_backend_access_token: str = Field(default="", exclude=True, repr=False)
    commercial_backend_access_token_file: str = Field(default="", exclude=True, repr=False)
    commercial_backend_site_code: str = "open-source"
    commercial_backend_site_name: str = "开源版"
    commercial_backend_timeout_seconds: int = 15
    # 三能力开关默认全部开启：商业版后端已通过幂等键与付费广告投放合约验证，
    # 开源版开箱即用。如需关闭某项能力，可在 .env 中显式设为 false。
    commercial_backend_mutation_idempotency_enabled: bool = True
    commercial_backend_payment_idempotency_enabled: bool = True
    commercial_backend_paid_ad_placement_enforced: bool = True
    commercial_frontend_url: str = ""
    commercial_admin_url: str = ""

    @property
    def mysql_runtime_url(self) -> URL:
        return URL.create(
            drivername="mysql+aiomysql",
            username=(self.mysql_app_user or self.mysql_user),
            password=(self.mysql_app_password or self.mysql_password),
            host=self.mysql_host,
            port=self.mysql_port,
            database=self.mysql_database,
            query={"charset": "utf8mb4"},
        )

    @property
    def mysql_migration_url(self) -> URL:
        return URL.create(
            drivername="mysql+aiomysql",
            username=(self.mysql_migration_user or self.mysql_user),
            password=(self.mysql_migration_password or self.mysql_password),
            host=self.mysql_host,
            port=self.mysql_port,
            database=self.mysql_database,
            query={"charset": "utf8mb4"},
        )

    @property
    def mysql_url(self) -> URL:
        """Backward-compatible alias for the runtime, never migration, URL."""

        return self.mysql_runtime_url

    @property
    def cors_origins_list(self) -> List[str]:
        """逗号分隔的 CORS 来源转列表。"""
        raw = (self.cors_allowed_origins or "").strip()
        if not raw:
            return []
        return [origin.strip() for origin in raw.split(",") if origin.strip()]

    @property
    def uploads_path(self) -> Path:
        value = Path(self.uploads_dir).expanduser()
        if not value.is_absolute():
            value = API_DIR / value
        return value.resolve()

    jwt_secret: str = Field(
        default="xianyu-assistant-jwt-secret-key-2026-04-22-very-long-secret-for-hmac-sha",
        exclude=True,
        repr=False,
    )
    jwt_secret_file: str = Field(default="", exclude=True, repr=False)
    # 12 hours by default. Long-lived access tokens materially increase the
    # impact of browser/storage compromise.
    jwt_expiration_ms: int = 43_200_000
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "xianyu-assistant-api"
    jwt_audience: str = "xianyu-assistant-web"
    cookie_crypto_secret: str = Field(
        default="dev-only-cookie-crypto-secret-change-me-32-chars",
        exclude=True,
        repr=False,
    )
    cookie_crypto_secret_file: str = Field(default="", exclude=True, repr=False)

    # 后台管理员账号配置（单租户精简版，不再依赖 Java 网关签发 JWT）
    admin_username: str = "admin"
    admin_password_hash: str = Field(default="", exclude=True, repr=False)
    admin_password_hash_file: str = Field(default="", exclude=True, repr=False)

    # crawler-service 基础地址（慢速搜索 / 浏览器爬取场景使用）
    crawler_base_url: str = "http://localhost:15178"

    # OpenAI 兼容 Embedding 服务配置（RAG 检索增强）
    embedding_api_key: str = Field(default="", exclude=True, repr=False)
    embedding_api_key_file: str = Field(default="", exclude=True, repr=False)
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_model: str = "text-embedding-3-small"

    # Redis 配置（缓存 / 限流 / 任务队列）
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str = Field(default="", exclude=True, repr=False)
    redis_password_file: str = Field(default="", exclude=True, repr=False)

    # Phase3: OpenAI-compatible AI provider. 未配置时自动走本地启发式兜底。
    ai_provider_enabled: bool = False
    ai_provider_base_url: str = ""
    ai_provider_api_key: str = Field(default="", exclude=True, repr=False)
    ai_provider_api_key_file: str = Field(default="", exclude=True, repr=False)
    ai_provider_model: str = "gpt-4o-mini"
    ai_provider_timeout_seconds: int = 30

    # 闲鱼 MTOP 接口配置（自动分类）
    xianyu_mtop_app_key: str = "34839810"
    xianyu_mtop_category_api: str = "mtop.taobao.idle.kgraph.property.recommend"
    xianyu_mtop_category_version: str = "2.0"
    xianyu_mtop_upload_url: str = "https://stream-upload.goofish.com/api/upload.api?floderId=0&appkey=xy_chat"

    # Optional read-only local homepage-content fallback directory.
    # Runtime mutations live in MySQL or the advertising bridge.
    STORAGE_DIR: str = "../data"

    # 自动分类置信度阈值
    # 注意：闲鱼官方 categoryPredictResult 已优先采用并跳过阈值检查，
    # 这里的阈值仅用于 score 排序回退场景。闲鱼实际返回的 score 普遍较低
    # （典型值 0.03~0.05），故降低阈值避免误判为低置信度。
    auto_category_min_score: float = 0.03
    auto_category_min_margin: float = 0.01

    # 安全配置
    login_max_attempts: int = 5          # 登录失败最大尝试次数
    login_lock_minutes: int = 15         # 锁定时长（分钟）
    api_rate_limit_per_minute: int = 600 # 单 IP API 请求上限，0 表示关闭
    audit_log_retention_days: int = 90   # 审计日志保留天数
    audit_mutation_intent_required: bool = False
    docs_enabled: bool = True            # 是否开放 /docs /redoc
    max_request_body_bytes: int = 12 * 1024 * 1024
    max_upload_bytes: int = 10 * 1024 * 1024
    max_rag_document_bytes: int = 5 * 1024 * 1024
    # GitHub 仓库地址（owner/repo 格式），用于检查更新
    github_repo: str = "dameng2026/XianYuPilo"
    # 阿里云 ACR 镜像源（可选，留空则不展示此镜像源 tab）
    acr_registry_url: str = ""
    # Only trust forwarded client IP headers when the direct socket peer is in
    # one of these comma-separated IPs/CIDRs.
    trusted_proxy_ips: str = ""
    trusted_hosts: str = ""

    @property
    def is_production_like(self) -> bool:
        return (self.app_env or "dev").strip().lower() in {
            "prod", "production", "staging", "stage",
        }

    @property
    def trusted_proxy_list(self) -> List[str]:
        return [item.strip() for item in (self.trusted_proxy_ips or "").split(",") if item.strip()]

    @property
    def trusted_hosts_list(self) -> List[str]:
        return [item.strip() for item in (self.trusted_hosts or "").split(",") if item.strip()]


    @model_validator(mode="after")
    def validate_security_defaults(self):
        for role, user, password in (
            ("MYSQL_APP", self.mysql_app_user, self.mysql_app_password),
            ("MYSQL_MIGRATION", self.mysql_migration_user, self.mysql_migration_password),
        ):
            if bool((user or "").strip()) != bool(password):
                raise ValueError(f"{role}_USER and {role}_PASSWORD must be configured together")
        if self.mysql_app_user and self.mysql_migration_user:
            if self.mysql_app_user.casefold() == self.mysql_migration_user.casefold():
                raise ValueError("MYSQL_APP_USER and MYSQL_MIGRATION_USER must be different")
        if self.mysql_app_password and self.mysql_migration_password:
            if self.mysql_app_password == self.mysql_migration_password:
                raise ValueError("MYSQL_APP_PASSWORD and MYSQL_MIGRATION_PASSWORD must be different")
        if self.jwt_algorithm not in {"HS256", "HS384", "HS512"}:
            raise ValueError("JWT_ALGORITHM must be one of HS256/HS384/HS512")
        if not 300_000 <= self.jwt_expiration_ms <= 86_400_000:
            raise ValueError("JWT_EXPIRATION_MS must be between 5 minutes and 24 hours")
        if not 1 <= self.login_max_attempts <= 100:
            raise ValueError("LOGIN_MAX_ATTEMPTS must be between 1 and 100")
        if not 1 <= self.login_lock_minutes <= 1440:
            raise ValueError("LOGIN_LOCK_MINUTES must be between 1 and 1440")
        if not 0 <= self.api_rate_limit_per_minute <= 100_000:
            raise ValueError("API_RATE_LIMIT_PER_MINUTE must be between 0 and 100000")
        if not 1_048_576 <= self.max_request_body_bytes <= 104_857_600:
            raise ValueError("MAX_REQUEST_BODY_BYTES must be between 1MB and 100MB")
        if not 1_048_576 <= self.max_upload_bytes <= self.max_request_body_bytes:
            raise ValueError("MAX_UPLOAD_BYTES must be between 1MB and MAX_REQUEST_BODY_BYTES")
        if not 65_536 <= self.max_rag_document_bytes <= self.max_upload_bytes:
            raise ValueError("MAX_RAG_DOCUMENT_BYTES must be between 64KB and MAX_UPLOAD_BYTES")
        if not 1 <= self.mysql_connect_timeout_seconds <= 120:
            raise ValueError("MYSQL_CONNECT_TIMEOUT_SECONDS must be between 1 and 120")
        if not 1 <= self.mysql_read_timeout_seconds <= 3600:
            raise ValueError("MYSQL_READ_TIMEOUT_SECONDS must be between 1 and 3600")
        if not 1 <= self.mysql_write_timeout_seconds <= 3600:
            raise ValueError("MYSQL_WRITE_TIMEOUT_SECONDS must be between 1 and 3600")
        if not 1 <= self.db_pool_size <= 100:
            raise ValueError("DB_POOL_SIZE must be between 1 and 100")
        if not 0 <= self.db_max_overflow <= 200:
            raise ValueError("DB_MAX_OVERFLOW must be between 0 and 200")
        if not 1 <= self.db_pool_timeout_seconds <= 300:
            raise ValueError("DB_POOL_TIMEOUT_SECONDS must be between 1 and 300")
        if not 60 <= self.db_pool_recycle_seconds <= 86_400:
            raise ValueError("DB_POOL_RECYCLE_SECONDS must be between 60 and 86400")
        if not 1 <= self.scheduler_poll_interval_seconds <= 300:
            raise ValueError("SCHEDULER_POLL_INTERVAL_SECONDS must be between 1 and 300")
        if not 10 <= self.scheduler_task_timeout_seconds <= 3600:
            raise ValueError("SCHEDULER_TASK_TIMEOUT_SECONDS must be between 10 and 3600")
        if not self.scheduler_task_timeout_seconds + 30 <= self.scheduler_lease_seconds <= 7200:
            raise ValueError(
                "SCHEDULER_LEASE_SECONDS must be at least 30 seconds longer than "
                "SCHEDULER_TASK_TIMEOUT_SECONDS and no more than 7200"
            )
        if not 1 <= self.scheduler_batch_size <= 100:
            raise ValueError("SCHEDULER_BATCH_SIZE must be between 1 and 100")
        if not 60 <= self.delivery_recovery_interval_seconds <= 86_400:
            raise ValueError(
                "DELIVERY_RECOVERY_INTERVAL_SECONDS must be between 60 and 86400"
            )
        if not 1 <= self.delivery_recovery_batch_size <= 200:
            raise ValueError("DELIVERY_RECOVERY_BATCH_SIZE must be between 1 and 200")
        if not 0 <= self.delivery_recovery_min_age_seconds <= 86_400:
            raise ValueError(
                "DELIVERY_RECOVERY_MIN_AGE_SECONDS must be between 0 and 86400"
            )
        if not 1 <= self.audit_log_retention_days <= 3650:
            raise ValueError("AUDIT_LOG_RETENTION_DAYS must be between 1 and 3650")

        origins: list[str] = []
        for origin in self.cors_origins_list:
            parsed = urlparse(origin)
            if (
                origin == "*"
                or parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or parsed.path not in {"", "/"}
            ):
                raise ValueError(f"Invalid CORS origin: {origin!r}")
            origins.append(origin.rstrip("/"))
        self.cors_allowed_origins = ",".join(dict.fromkeys(origins))

        for field_name, value in {
            "COMMERCIAL_BACKEND_BASE_URL": self.commercial_backend_base_url,
            "COMMERCIAL_FRONTEND_URL": self.commercial_frontend_url,
            "COMMERCIAL_ADMIN_URL": self.commercial_admin_url,
        }.items():
            normalized = (value or "").strip()
            if not normalized:
                continue
            parsed = urlparse(normalized)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.fragment
            ):
                raise ValueError(f"{field_name} must be a valid HTTP(S) URL without credentials")
            if self.is_production_like and parsed.scheme != "https":
                raise ValueError(f"{field_name} must use HTTPS in prod/staging")

        if self.is_production_like:
            insecure_values = {
                "please-change-this-to-a-random-32-char-string",
                "dev-only-internal-api-token-change-me-32-chars",
                "dev-only-cookie-crypto-secret-change-me-32-chars",
                "xianyu-assistant-jwt-secret-key-2026-04-22-very-long-secret-for-hmac-sha",
            }
            for field_name, value in {
                "JWT_SECRET": self.jwt_secret,
                "COOKIE_CRYPTO_SECRET": self.cookie_crypto_secret,
                "INTERNAL_API_TOKEN": self.internal_api_token,
            }.items():
                normalized = (value or "").strip()
                if len(normalized) < 32 or normalized in insecure_values:
                    raise ValueError(
                        f"{field_name} must be a unique random value (>=32 chars) in prod/staging"
                    )
            if not (self.admin_password_hash or "").startswith(("$2a$", "$2b$", "$2y$")):
                raise ValueError("ADMIN_PASSWORD_HASH must be a bcrypt hash in prod/staging")
            if not (self.redis_password or "").strip():
                raise ValueError("REDIS_PASSWORD must be configured in prod/staging")
            for role, url in (
                ("MYSQL runtime", self.mysql_runtime_url),
                ("MYSQL migration", self.mysql_migration_url),
            ):
                mysql_password = (url.password or "").strip()
                if len(mysql_password) < 16 or mysql_password in {
                    "xianyu_pass",
                    "password",
                    "root",
                }:
                    raise ValueError(f"{role} password must be strong in prod/staging")
                if (url.username or "").strip().casefold() in {"", "root", "mysql", "admin"}:
                    raise ValueError(f"{role} user must be a dedicated non-root account")
            if self.commercial_backend_access_token and len(self.commercial_backend_access_token.strip()) < 32:
                raise ValueError("COMMERCIAL_BACKEND_ACCESS_TOKEN must be at least 32 chars")
            if not self.trusted_hosts_list or "*" in self.trusted_hosts_list:
                raise ValueError("TRUSTED_HOSTS must contain explicit hostnames in prod/staging")
            if not self.audit_mutation_intent_required:
                raise ValueError(
                    "AUDIT_MUTATION_INTENT_REQUIRED must be true in prod/staging"
                )
            if self.docs_enabled:
                raise ValueError(
                    "DOCS_ENABLED must be false in prod/staging to avoid exposing API surface"
                )
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: Any,
        env_settings: Any,
        dotenv_settings: Any,
        file_secret_settings: Any,
    ) -> tuple[Any, ...]:
        del settings_cls

        def file_aware_settings() -> dict[str, Any]:
            # Source priority remains init > environment > dotenv > secrets_dir.
            merged: dict[str, Any] = {}
            for source in (
                file_secret_settings,
                dotenv_settings,
                env_settings,
                init_settings,
            ):
                merged.update(source())
            return resolve_file_secret_values(
                merged,
                secret_fields=FILE_BACKED_SECRET_FIELDS,
                optional_fields=OPTIONAL_FILE_BACKED_SECRET_FIELDS,
            )

        return (file_aware_settings,)

    model_config = SettingsConfigDict(
        env_file=str(ROOT_ENV_FILE),
        env_file_encoding="utf-8",
        hide_input_in_errors=True,
        extra="ignore",
    )


# 开源版内置商业桥接默认值：用户下载部署后无需配置即可直连商业版后端。
# 敏感值（token、后端地址、前台引流地址）以混淆编码存储，运行时解码，不以明文出现在源码中。
# 混淆方式：base64(XOR(plaintext, sha256(seed)))，仅防止随意查看，非密码学加密。
# token 轮换时需重新生成编码值并同步商业版 .env.production 的 OPEN_SOURCE_BRIDGE_TOKEN。
# 重新生成编码值的方法见本文件末尾的 _regenerate_bridge_encoding 函数。
# 详见 .trae/rules/opensource-commercial-bridge-sync.md
def _resolve_bridge_value(encoded: str, seed: str) -> str:
    """运行时解码内置桥接默认值。"""
    raw = base64.b64decode(encoded.encode("ascii"))
    key = hashlib.sha256(seed.encode("utf-8")).digest()
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(raw)).decode("utf-8")


_BRIDGE_ENC_TOKEN = "0U34kM8JkBvWvFRjcJp3cCsYy3XY+0TCFYEu4L9fAa/uV+y/6gG4A7ydeFx1vWorLRLvFP36QOwxrCPZoWh4oA=="
_BRIDGE_ENC_BACKEND = "noGcALZ+NUyrnGCFtSMSZ3WsvzSbI7znFXRP"
_BRIDGE_ENC_FRONTEND = "zVB6D3X4NZfv2QYII2yZz0TaNJanh3S97d8j"
_BRIDGE_SEED_TOKEN = "xianyu.bridge.token.3a1c9786"
_BRIDGE_SEED_BACKEND = "xianyu.backend.url.e1c1b20b"
_BRIDGE_SEED_FRONTEND = "xianyu.frontend.url.e1896305"

_BUILTIN_BRIDGE_DEFAULTS: dict[str, str] = {
    "commercial_backend_base_url": _resolve_bridge_value(_BRIDGE_ENC_BACKEND, _BRIDGE_SEED_BACKEND),
    "commercial_backend_access_token": _resolve_bridge_value(_BRIDGE_ENC_TOKEN, _BRIDGE_SEED_TOKEN),
    "commercial_frontend_url": _resolve_bridge_value(_BRIDGE_ENC_FRONTEND, _BRIDGE_SEED_FRONTEND),
}


def _apply_builtin_bridge_defaults(target: "Settings") -> "Settings":
    """Apply built-in commercial bridge defaults after env/file resolution.

    This runs AFTER validate_security_defaults so the HTTPS-in-production check
    does not reject the built-in HTTP backend URL. Env vars and secret files
    always take precedence — defaults only fill in empty values.
    """
    if not (getattr(target, "commercial_backend_base_url", "") or "").strip():
        target.commercial_backend_base_url = _BUILTIN_BRIDGE_DEFAULTS["commercial_backend_base_url"]
    if not (getattr(target, "commercial_backend_access_token", "") or "").strip():
        target.commercial_backend_access_token = _BUILTIN_BRIDGE_DEFAULTS["commercial_backend_access_token"]
    if not (getattr(target, "commercial_frontend_url", "") or "").strip():
        target.commercial_frontend_url = _BUILTIN_BRIDGE_DEFAULTS["commercial_frontend_url"]
    return target


def _regenerate_bridge_encoding(token: str, backend_url: str, frontend_url: str) -> dict[str, str]:
    """生成新的混淆编码值，用于 token 轮换。

    用法：
      1. 调用本函数生成新的编码值与种子
      2. 将输出更新到 _BRIDGE_ENC_* 和 _BRIDGE_SEED_* 常量
      3. 同步更新商业版 .env.production 的 OPEN_SOURCE_BRIDGE_TOKEN
    """
    import secrets

    new_seed_token = f"xianyu.bridge.token.{secrets.token_hex(4)}"
    new_seed_backend = f"xianyu.backend.url.{secrets.token_hex(4)}"
    new_seed_frontend = f"xianyu.frontend.url.{secrets.token_hex(4)}"

    def _encode(plain: str, seed: str) -> str:
        raw = plain.encode("utf-8")
        key = hashlib.sha256(seed.encode("utf-8")).digest()
        xored = bytes(b ^ key[i % len(key)] for i, b in enumerate(raw))
        return base64.b64encode(xored).decode("ascii")

    return {
        "_BRIDGE_ENC_TOKEN": _encode(token, new_seed_token),
        "_BRIDGE_ENC_BACKEND": _encode(backend_url, new_seed_backend),
        "_BRIDGE_ENC_FRONTEND": _encode(frontend_url, new_seed_frontend),
        "_BRIDGE_SEED_TOKEN": new_seed_token,
        "_BRIDGE_SEED_BACKEND": new_seed_backend,
        "_BRIDGE_SEED_FRONTEND": new_seed_frontend,
    }


settings = Settings()
_apply_builtin_bridge_defaults(settings)
