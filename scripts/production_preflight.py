#!/usr/bin/env python3
"""Fail-closed validation for the production .env file.

The validator deliberately reports variable names and rules only. It never
prints configuration values, so it is safe to run in CI and deployment logs.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse


SECRET_FIELDS = (
    "MYSQL_ROOT_PASSWORD",
    "MYSQL_APP_PASSWORD",
    "MYSQL_MIGRATION_PASSWORD",
    "REDIS_PASSWORD",
    "JWT_SECRET",
    "COOKIE_CRYPTO_SECRET",
    "INTERNAL_API_TOKEN",
)
REQUIRED_FILE_SECRET_FIELDS = (
    "ADMIN_PASSWORD_HASH",
    *SECRET_FIELDS,
)
OPTIONAL_FILE_SECRET_FIELDS = (
    "COMMERCIAL_BACKEND_ACCESS_TOKEN",
    "EMBEDDING_API_KEY",
    "AI_PROVIDER_API_KEY",
)
FILE_SECRET_FIELDS = REQUIRED_FILE_SECRET_FIELDS + OPTIONAL_FILE_SECRET_FIELDS
MAX_SECRET_FILE_BYTES = 8_192
PLACEHOLDER_FRAGMENTS = (
    "changeme",
    "change-me",
    "please-change",
    "replace-with",
    "example-secret",
    "your-secret",
    "dev-only",
    "password123",
)
HOST_RE = re.compile(
    r"^(?=.{1,253}\.?$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.?$",
    re.IGNORECASE,
)
BCRYPT_RE = re.compile(r"^\$2[aby]\$(\d{2})\$[./A-Za-z0-9]{53}$")


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def error(self, key: str, rule: str) -> None:
        self.errors.append(f"{key}: {rule}")

    def warn(self, key: str, rule: str) -> None:
        self.warnings.append(f"{key}: {rule}")


def load_env(
    path: Path,
    report: ValidationReport,
    *,
    require_secret_files: bool = False,
) -> dict[str, str]:
    if not path.is_file():
        report.error("ENV_FILE", "file does not exist")
        return {}

    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        report.error("ENV_FILE", "must be a readable UTF-8 text file")
        return {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            report.error(f"ENV_FILE line {line_number}", "expected KEY=VALUE")
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            report.error(f"ENV_FILE line {line_number}", "invalid variable name")
            continue
        if key in values:
            report.error(key, "is defined more than once")
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value

    for field_name in FILE_SECRET_FIELDS:
        direct_value = values.get(field_name, "")
        file_reference = values.get(f"{field_name}_FILE", "")
        if direct_value and file_reference:
            report.error(
                field_name,
                f"must use exactly one of {field_name} or {field_name}_FILE",
            )
            continue
        if file_reference:
            resolved = _read_secret_file(
                path,
                file_reference,
                report,
                field_name,
                allow_empty=field_name in OPTIONAL_FILE_SECRET_FIELDS,
            )
            if resolved is not None:
                values[field_name] = resolved
            continue
        if require_secret_files:
            report.error(
                f"{field_name}_FILE",
                "must reference a protected secret file in production (use an empty file only for a disabled optional integration)",
            )
    return values


def _read_secret_file(
    env_path: Path,
    reference: str,
    report: ValidationReport,
    field_name: str,
    *,
    allow_empty: bool,
) -> str | None:
    candidate = Path(reference)
    if not candidate.is_absolute():
        candidate = env_path.parent / candidate
    try:
        if candidate.is_symlink():
            report.error(f"{field_name}_FILE", "must not be a symbolic link")
            return None
        stat_result = candidate.stat()
    except OSError:
        report.error(f"{field_name}_FILE", "must be a readable regular file")
        return None
    if not candidate.is_file():
        report.error(f"{field_name}_FILE", "must be a readable regular file")
        return None
    if stat_result.st_size > MAX_SECRET_FILE_BYTES:
        report.error(f"{field_name}_FILE", "exceeds the maximum permitted size")
        return None
    if os.name != "nt" and stat.S_IMODE(stat_result.st_mode) & 0o077:
        report.error(f"{field_name}_FILE", "must not grant group or other access")
        return None
    try:
        raw = candidate.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeError):
        report.error(f"{field_name}_FILE", "must be readable UTF-8 text")
        return None
    if text.endswith("\r\n"):
        text = text[:-2]
    elif text.endswith("\n"):
        text = text[:-1]
    if "\x00" in text or "\n" in text or "\r" in text:
        report.error(f"{field_name}_FILE", "must contain exactly one logical line")
        return None
    if text != text.strip():
        report.error(f"{field_name}_FILE", "must not contain surrounding whitespace")
        return None
    if not text and not allow_empty:
        report.error(f"{field_name}_FILE", "must not be empty")
        return None
    return text


def validate(values: dict[str, str], env_path: Path | None = None) -> ValidationReport:
    report = ValidationReport()

    if values.get("APP_ENV", "").strip().lower() not in {"prod", "production"}:
        report.error("APP_ENV", "must be production")

    if not values.get("ADMIN_USERNAME", "").strip():
        report.error("ADMIN_USERNAME", "must not be empty")
    password_hash = values.get("ADMIN_PASSWORD_HASH", "").strip()
    match = BCRYPT_RE.fullmatch(password_hash)
    if not match:
        report.error("ADMIN_PASSWORD_HASH", "must be a complete bcrypt hash")
    elif int(match.group(1)) < 12:
        report.error("ADMIN_PASSWORD_HASH", "bcrypt cost must be 12 or higher")

    database = values.get("MYSQL_DATABASE", "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", database):
        report.error("MYSQL_DATABASE", "must be a 1-64 character SQL identifier")
    database_users: dict[str, str] = {}
    for key in ("MYSQL_APP_USER", "MYSQL_MIGRATION_USER"):
        database_user = values.get(key, "").strip()
        database_users[key] = database_user
        if not re.fullmatch(r"[A-Za-z0-9_]{1,32}", database_user):
            report.error(key, "must be a 1-32 character SQL identifier")
        elif database_user.lower() in {"root", "mysql", "admin"}:
            report.error(key, "must be a dedicated non-administrative database user")
    if (
        database_users["MYSQL_APP_USER"]
        and database_users["MYSQL_MIGRATION_USER"]
        and database_users["MYSQL_APP_USER"].casefold()
        == database_users["MYSQL_MIGRATION_USER"].casefold()
    ):
        report.error(
            "MYSQL_DATABASE_USERS",
            "runtime and migration identities must be different",
        )

    minimum_lengths = {
        "MYSQL_ROOT_PASSWORD": 16,
        "MYSQL_APP_PASSWORD": 16,
        "MYSQL_MIGRATION_PASSWORD": 16,
        "REDIS_PASSWORD": 16,
        "JWT_SECRET": 32,
        "COOKIE_CRYPTO_SECRET": 32,
        "INTERNAL_API_TOKEN": 32,
    }
    for key, minimum in minimum_lengths.items():
        value = values.get(key, "").strip()
        if len(value) < minimum:
            report.error(key, f"must contain at least {minimum} characters")
            continue
        if _looks_like_placeholder(value):
            report.error(key, "must not use a documented/example placeholder")
        if len(set(value)) < 10:
            report.error(key, "has insufficient character diversity")

    redis_password = values.get("REDIS_PASSWORD", "").strip()
    if redis_password and not re.fullmatch(r"[A-Za-z0-9_-]{16,256}", redis_password):
        report.error(
            "REDIS_PASSWORD",
            "must use 16-256 Base64URL characters so generated Redis configuration is unambiguous",
        )

    populated_secrets = [values.get(key, "").strip() for key in SECRET_FIELDS]
    if len([value for value in populated_secrets if value]) != len(set(value for value in populated_secrets if value)):
        report.error("SECRETS", "every database, Redis, JWT, cookie, and internal token secret must be unique")
    if (
        values.get("MYSQL_APP_PASSWORD", "").strip()
        and values.get("MYSQL_APP_PASSWORD", "").strip()
        == values.get("MYSQL_MIGRATION_PASSWORD", "").strip()
    ):
        report.error(
            "MYSQL_DATABASE_PASSWORDS",
            "runtime and migration credentials must be different",
        )

    _validate_integer(values, report, "JWT_EXPIRATION_MS", 300_000, 86_400_000)
    _validate_integer(values, report, "LOGIN_MAX_ATTEMPTS", 1, 100)
    _validate_integer(values, report, "LOGIN_LOCK_MINUTES", 1, 1_440)
    _validate_integer(values, report, "API_RATE_LIMIT_PER_MINUTE", 1, 100_000)
    _validate_integer(values, report, "MAX_REQUEST_BODY_BYTES", 1_048_576, 104_857_600)
    _validate_integer(values, report, "MAX_UPLOAD_BYTES", 1_048_576, 104_857_600)
    _validate_integer(values, report, "WEB_PORT", 1, 65_535)
    try:
        body_limit = int(values.get("MAX_REQUEST_BODY_BYTES", ""))
        upload_limit = int(values.get("MAX_UPLOAD_BYTES", ""))
        if upload_limit > body_limit:
            report.error("MAX_UPLOAD_BYTES", "must not exceed MAX_REQUEST_BODY_BYTES")
    except ValueError:
        pass

    trusted_hosts = _csv(values.get("TRUSTED_HOSTS", ""))
    if not trusted_hosts:
        report.error("TRUSTED_HOSTS", "must contain an explicit hostname allowlist")
    if "*" in trusted_hosts:
        report.error("TRUSTED_HOSTS", "wildcards are not allowed in production")

    trusted_proxies = _csv(values.get("TRUSTED_PROXY_IPS", ""))
    if not trusted_proxies:
        report.error("TRUSTED_PROXY_IPS", "must contain the reverse proxy IP/CIDR allowlist")
    normalized_proxies: set[str] = set()
    for proxy in trusted_proxies:
        try:
            network = ipaddress.ip_network(proxy, strict=False)
        except ValueError:
            report.error("TRUSTED_PROXY_IPS", "must contain only valid IP addresses or CIDRs")
            break
        normalized_proxies.add(network.with_prefixlen)
        if network.prefixlen != network.max_prefixlen:
            report.error(
                "TRUSTED_PROXY_IPS",
                "must trust explicit proxy host addresses only, never a shared container subnet",
            )
            break
    if normalized_proxies and normalized_proxies != {"172.30.240.20/32"}:
        report.error(
            "TRUSTED_PROXY_IPS",
            "must exactly match the fixed Web proxy address 172.30.240.20/32 in the reference Compose topology",
        )

    for origin in _csv(values.get("CORS_ALLOWED_ORIGINS", "")):
        _validate_origin(origin, report, "CORS_ALLOWED_ORIGINS", permit_local_http=True)
    if "*" in _csv(values.get("CORS_ALLOWED_ORIGINS", "")):
        report.error("CORS_ALLOWED_ORIGINS", "wildcards are not allowed")

    crawler_hosts = _csv(values.get("CRAWLER_ALLOWED_TARGET_HOSTS", ""))
    if not crawler_hosts:
        report.error("CRAWLER_ALLOWED_TARGET_HOSTS", "must contain at least one domain")
    for hostname in crawler_hosts:
        normalized = hostname.rstrip(".").lower()
        try:
            ipaddress.ip_address(normalized)
        except ValueError:
            is_ip = False
        else:
            is_ip = True
        if is_ip or normalized == "localhost" or "*" in normalized or not HOST_RE.fullmatch(normalized):
            report.error("CRAWLER_ALLOWED_TARGET_HOSTS", "must contain only exact registrable domains")
            break

    for origin in _csv(values.get("CRAWLER_ALLOWED_ORIGINS", "")):
        _validate_origin(origin, report, "CRAWLER_ALLOWED_ORIGINS", permit_local_http=False)
    if "*" in _csv(values.get("CRAWLER_ALLOWED_ORIGINS", "")):
        report.error("CRAWLER_ALLOWED_ORIGINS", "wildcards are not allowed")

    if values.get("CRAWLER_FORCE_HEADLESS", "").strip().lower() not in {"1", "true", "yes", "on"}:
        report.error("CRAWLER_FORCE_HEADLESS", "must be true in production")
    if values.get("AUDIT_MUTATION_INTENT_REQUIRED", "").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        report.error(
            "AUDIT_MUTATION_INTENT_REQUIRED",
            "must be explicitly enabled in production",
        )
    if not _valid_byte_size(values.get("CRAWLER_JSON_LIMIT", ""), 1_024, 1_048_576):
        report.error("CRAWLER_JSON_LIMIT", "must resolve to 1KB-1MB")

    for key in (
        "COMMERCIAL_FRONTEND_URL",
        "COMMERCIAL_ADMIN_URL",
        "COMMERCIAL_BACKEND_BASE_URL",
    ):
        if values.get(key, "").strip():
            _validate_https_url(values[key], report, key)
    if values.get("COMMERCIAL_BACKEND_ACCESS_TOKEN", "").strip() and not values.get(
        "COMMERCIAL_BACKEND_BASE_URL", ""
    ).strip():
        report.error("COMMERCIAL_BACKEND_BASE_URL", "is required when COMMERCIAL_BACKEND_ACCESS_TOKEN is set")
    if values.get("COMMERCIAL_BACKEND_ACCESS_TOKEN", "").strip() and values.get(
        "COMMERCIAL_BACKEND_MUTATION_IDEMPOTENCY_ENABLED", ""
    ).strip().lower() not in {"1", "true", "yes", "on"}:
        report.error(
            "COMMERCIAL_BACKEND_MUTATION_IDEMPOTENCY_ENABLED",
            "must be enabled only after ad-application creation honors the same header/body idempotency key",
        )
    if values.get("COMMERCIAL_BACKEND_ACCESS_TOKEN", "").strip() and values.get(
        "COMMERCIAL_BACKEND_PAYMENT_IDEMPOTENCY_ENABLED", ""
    ).strip().lower() not in {"1", "true", "yes", "on"}:
        report.error(
            "COMMERCIAL_BACKEND_PAYMENT_IDEMPOTENCY_ENABLED",
            "must be enabled only after create and close both honor the same header/body idempotency key",
        )
    if values.get("COMMERCIAL_BACKEND_ACCESS_TOKEN", "").strip() and values.get(
        "COMMERCIAL_BACKEND_PAID_AD_PLACEMENT_ENFORCED", ""
    ).strip().lower() not in {"1", "true", "yes", "on"}:
        report.error(
            "COMMERCIAL_BACKEND_PAID_AD_PLACEMENT_ENFORCED",
            "must be enabled only after integration tests prove unpaid, closed, and expired ad orders cannot become active or appear in ad-serving responses",
        )
    if values.get("EMBEDDING_API_KEY", "").strip():
        _validate_https_url(values.get("EMBEDDING_BASE_URL", ""), report, "EMBEDDING_BASE_URL")
    if values.get("AI_PROVIDER_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}:
        _validate_https_url(values.get("AI_PROVIDER_BASE_URL", ""), report, "AI_PROVIDER_BASE_URL")
        if not values.get("AI_PROVIDER_API_KEY", "").strip():
            report.error("AI_PROVIDER_API_KEY", "is required when AI_PROVIDER_ENABLED is true")

    bind_address = values.get("WEB_BIND_ADDRESS", "127.0.0.1").strip()
    public_url = values.get("PUBLIC_BASE_URL", "").strip()
    try:
        bind_ip = ipaddress.ip_address(bind_address)
    except ValueError:
        report.error("WEB_BIND_ADDRESS", "must be a literal IPv4 or IPv6 address")
    else:
        if not bind_ip.is_loopback:
            report.error(
                "WEB_BIND_ADDRESS",
                "must remain loopback-only behind the reviewed same-host TLS proxy",
            )
    if not public_url:
        report.error("PUBLIC_BASE_URL", "the real public HTTPS origin is required")
    else:
        _validate_origin(
            public_url,
            report,
            "PUBLIC_BASE_URL",
            permit_local_http=False,
        )
        public_host = (urlparse(public_url).hostname or "").rstrip(".").lower()
        normalized_trusted_hosts = {
            host.rstrip(".").lower() for host in trusted_hosts
        }
        if public_host and public_host not in normalized_trusted_hosts:
            report.error(
                "TRUSTED_HOSTS",
                "must explicitly contain the PUBLIC_BASE_URL hostname",
            )

    if env_path is not None and os.name != "nt" and env_path.exists():
        permissions = stat.S_IMODE(env_path.stat().st_mode)
        if permissions & 0o077:
            report.error("ENV_FILE", "must not grant group or other access because it contains secrets")

    return report


def _looks_like_placeholder(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9-]", "", value.lower())
    return any(fragment in normalized for fragment in PLACEHOLDER_FRAGMENTS)


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _validate_integer(
    values: dict[str, str], report: ValidationReport, key: str, minimum: int, maximum: int
) -> None:
    try:
        value = int(values.get(key, ""))
    except ValueError:
        report.error(key, "must be an integer")
        return
    if not minimum <= value <= maximum:
        report.error(key, f"must be between {minimum} and {maximum}")


def _validate_origin(
    value: str, report: ValidationReport, key: str, *, permit_local_http: bool
) -> None:
    parsed = urlparse(value)
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    allowed_scheme = parsed.scheme == "https" or (permit_local_http and local and parsed.scheme == "http")
    if (
        not allowed_scheme
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        report.error(key, "must contain only explicit HTTPS origins (local HTTP is allowed only for CORS)")


def _validate_https_url(value: str, report: ValidationReport, key: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        report.error(key, "must be an absolute HTTPS URL without embedded credentials")


def _valid_byte_size(raw: str, minimum: int, maximum: int) -> bool:
    match = re.fullmatch(r"(\d+)(b|kb|mb)?", raw.strip().lower())
    if not match:
        return False
    multiplier = {None: 1, "b": 1, "kb": 1_024, "mb": 1_048_576}[match.group(2)]
    value = int(match.group(1)) * multiplier
    return minimum <= value <= maximum


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate production environment without printing secrets")
    parser.add_argument("--env-file", default=".env", help="path to the production env file")
    parser.add_argument("--quiet", action="store_true", help="print only failures")
    args = parser.parse_args(argv)

    env_path = Path(args.env_file).resolve()
    parse_report = ValidationReport()
    values = load_env(env_path, parse_report, require_secret_files=True)
    report = validate(values, env_path)
    report.errors[:0] = parse_report.errors
    report.warnings[:0] = parse_report.warnings

    for error in report.errors:
        print(f"ERROR: {error}", file=sys.stderr)
    if not args.quiet:
        for warning in report.warnings:
            print(f"WARNING: {warning}", file=sys.stderr)
    if report.errors:
        print(f"Production preflight failed with {len(report.errors)} error(s).", file=sys.stderr)
        return 2
    if not args.quiet:
        print("Production environment preflight passed; no secret values were displayed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
