from __future__ import annotations

import asyncio
import io
import ipaddress
import json
import os
import socket
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urljoin, urlparse
from uuid import uuid4

import httpx
from fastapi import UploadFile
from PIL import Image, ImageOps

from .atomic_file import atomic_replace_with_retry
from .config import settings

UPLOADS_DIR = settings.uploads_path
ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "GIF", "WEBP", "BMP"}
IMAGE_FORMAT_METADATA = {
    "JPEG": (".jpg", "image/jpeg"),
    "PNG": (".png", "image/png"),
    "GIF": (".gif", "image/gif"),
    "WEBP": (".webp", "image/webp"),
    "BMP": (".bmp", "image/bmp"),
}
SAFE_IMAGE_MEDIA_TYPES = frozenset(
    metadata[1] for metadata in IMAGE_FORMAT_METADATA.values()
)
MAX_IMAGE_PIXELS = 40_000_000


class UnsafePathError(ValueError):
    pass


class UnsafeRemoteURLError(ValueError):
    pass


class UploadValidationError(ValueError):
    pass


def _canonical_path_text(path: Path) -> str:
    """Return a comparison-safe absolute path, including on Windows races."""

    value = str(Path(path).resolve())
    if os.name == "nt":
        # ``Path.resolve`` may start returning the Win32 extended-length form
        # after another thread creates a previously missing parent.  Both forms
        # name the same file and must compare identically for containment.
        if value.startswith("\\\\?\\UNC\\"):
            value = "\\\\" + value[8:]
        elif value.startswith("\\\\?\\"):
            value = value[4:]
    return os.path.normcase(os.path.abspath(value))


def _assert_upload_containment(path: Path, *, allow_root: bool = False) -> Path:
    uploads_root = UPLOADS_DIR.resolve()
    candidate = Path(path).resolve()
    root_text = _canonical_path_text(uploads_root)
    candidate_text = _canonical_path_text(candidate)
    try:
        common = os.path.commonpath((root_text, candidate_text))
    except ValueError as exc:
        raise UnsafePathError("非法上传文件路径") from exc
    if common != root_text or (not allow_root and candidate_text == root_text):
        raise UnsafePathError("非法上传文件路径")
    return candidate


@dataclass(frozen=True, slots=True)
class _PinnedRemoteTarget:
    """One already-validated network endpoint for an outbound image request."""

    original_url: str
    connect_url: str
    host_header: str
    sni_hostname: str


@dataclass(frozen=True, slots=True)
class PublicHTTPResponse:
    """Bounded response returned by a DNS-pinned public HTTPS request."""

    status_code: int
    headers: httpx.Headers
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> object:
        return json.loads(self.text)


def resolve_upload_path(relative_path: str) -> Path:
    """Resolve an upload path and prove it remains inside UPLOADS_DIR."""
    raw = str(relative_path or "").replace("\\", "/").strip("/")
    parts = raw.split("/") if raw else []
    if (
        not parts
        or any(part in {"", ".", ".."} for part in parts)
        or any("\x00" in part or ":" in part for part in parts)
    ):
        raise UnsafePathError("非法上传文件路径")
    return _assert_upload_containment(UPLOADS_DIR.joinpath(*parts))


def write_upload_bytes_atomic(path: Path, data: bytes) -> None:
    """Durably publish one upload without exposing a partially written file.

    The destination must stay below the configured uploads root.  Each writer
    gets a unique same-directory temporary file, so concurrent cache fills for
    the same object cannot corrupt one another or collide on a process-wide
    temporary filename.
    """

    if not data:
        raise UploadValidationError("图片内容为空")
    destination = _assert_upload_containment(Path(path))

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.{uuid4().hex}.tmp"
    )
    try:
        with temporary.open("xb") as output:
            written = output.write(data)
            if written != len(data):
                raise OSError("incomplete upload write")
            output.flush()
            os.fsync(output.fileno())
        atomic_replace_with_retry(temporary, destination)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            # The original write/replace exception is more actionable. A
            # uniquely named stale temp is ignored by every public reader.
            pass


async def read_upload_limited(upload: UploadFile, max_bytes: int | None = None) -> bytes:
    limit = int(max_bytes or settings.max_upload_bytes)
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = await upload.read(min(64 * 1024, limit + 1 - size))
        if not chunk:
            break
        size += len(chunk)
        if size > limit:
            raise UploadValidationError(f"文件不能超过 {limit // 1024 // 1024}MB")
        chunks.append(chunk)
    return b"".join(chunks)


def validate_image_bytes(data: bytes) -> tuple[str, str]:
    if not data:
        raise UploadValidationError("图片内容为空")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
                image_format = str(image.format or "").upper()
                if image_format not in ALLOWED_IMAGE_FORMATS:
                    raise UploadValidationError("不支持的图片格式")
                if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                    raise UploadValidationError("图片像素尺寸过大")
                image.verify()
    except UploadValidationError:
        raise
    except (Image.UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as exc:
        raise UploadValidationError("图片文件损坏或格式不受支持") from exc
    return IMAGE_FORMAT_METADATA[image_format]


def normalize_image_bytes(
    data: bytes,
    *,
    max_bytes: int | None = None,
) -> tuple[bytes, str, str]:
    """Decode then re-encode an image to strip metadata and polyglot payloads."""
    limit = int(max_bytes or settings.max_upload_bytes)
    validate_image_bytes(data)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                source.seek(0)
                source.load()
                image = ImageOps.exif_transpose(source)
                image_format = str(source.format or "").upper()
                # Uploaded animations are flattened to their first frame. It
                # avoids decompression amplification across hundreds of frames
                # and keeps every stored asset independently verifiable.
                image.seek(0)
                image = image.copy()

        output = io.BytesIO()
        if image_format == "JPEG":
            if image.mode != "RGB":
                image = image.convert("RGB")
            image.save(output, format="JPEG", quality=90, optimize=True)
            extension, content_type = ".jpg", "image/jpeg"
        else:
            if image.mode not in {"1", "L", "LA", "P", "RGB", "RGBA"}:
                image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            image.save(output, format="PNG", optimize=True)
            extension, content_type = ".png", "image/png"
        normalized = output.getvalue()
    except (Image.UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as exc:
        raise UploadValidationError("图片文件损坏或无法安全解码") from exc
    if not normalized or len(normalized) > limit:
        raise UploadValidationError(f"处理后的图片不能超过 {limit // 1024 // 1024}MB")
    return normalized, extension, content_type


def load_safe_local_image(
    path: Path,
    *,
    max_bytes: int | None = None,
) -> tuple[bytes, str]:
    """Read a bounded local raster image and return bytes plus detected MIME.

    File extensions and browser MIME guessing are intentionally ignored. SVG,
    HTML and other active content are never served through the public uploads
    origin, even if a file was placed in the directory out of band.
    """

    limit = int(max_bytes or settings.max_upload_bytes)
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise UploadValidationError("图片文件不可读取") from exc
    if size <= 0 or size > limit:
        raise UploadValidationError(f"图片不能超过 {limit // 1024 // 1024}MB")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise UploadValidationError("图片文件不可读取") from exc
    _, media_type = validate_image_bytes(data)
    return data, media_type


async def download_trusted_origin_image(
    url: str,
    *,
    max_bytes: int | None = None,
    timeout_seconds: float = 15,
) -> tuple[bytes, str]:
    """Fetch and normalize a bounded image through DNS-pinned public HTTPS."""
    try:
        raw = await download_public_image(
            url, max_bytes=max_bytes, timeout_seconds=timeout_seconds, allow_redirects=False,
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise FileNotFoundError("商业图片不存在") from exc
        raise UploadValidationError("商业图片回源暂不可用") from exc
    normalized, _, media_type = await asyncio.to_thread(
        normalize_image_bytes, raw, max_bytes=max_bytes,
    )
    return normalized, media_type


def _is_public_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    return bool(
        ip.is_global
        and not ip.is_private
        and not ip.is_loopback
        and not ip.is_link_local
        and not ip.is_multicast
        and not ip.is_reserved
        and not ip.is_unspecified
    )


def _resolve_public_image_targets(
    url: str,
    *,
    require_https: bool = False,
) -> list[_PinnedRemoteTarget]:
    """Validate once and pin the connection URL to the resolved public IPs.

    Connecting to the original hostname after validation would perform a second
    DNS lookup and permit a DNS-rebinding race into loopback or metadata
    networks. The HTTP Host header and TLS SNI retain the validated hostname,
    while the TCP origin is the exact public address checked here.
    """

    value = str(url or "").strip()
    if not value or len(value) > 2048:
        raise UnsafeRemoteURLError("图片 URL 不能为空且不能超过 2048 个字符")
    parsed = urlparse(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise UnsafeRemoteURLError("仅支持公网 HTTP(S) 图片地址")
    if require_https and parsed.scheme.lower() != "https":
        raise UnsafeRemoteURLError("仅支持公网 HTTPS 图片地址")
    if parsed.username or parsed.password or parsed.fragment:
        raise UnsafeRemoteURLError("图片 URL 格式不安全")
    host = parsed.hostname.rstrip(".")
    if host.casefold() == "localhost" or host.casefold().endswith(".localhost"):
        raise UnsafeRemoteURLError("不允许访问本机或内网地址")
    try:
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except ValueError as exc:
        raise UnsafeRemoteURLError("图片 URL 端口无效") from exc
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
    except (socket.gaierror, UnicodeError, ValueError) as exc:
        raise UnsafeRemoteURLError("图片地址无法解析") from exc
    if not addresses or any(not _is_public_ip(address) for address in addresses):
        raise UnsafeRemoteURLError("不允许访问本机或内网地址")

    ascii_host = host.encode("idna").decode("ascii")
    host_for_header = f"[{ascii_host}]" if ":" in ascii_host else ascii_host
    default_port = 443 if parsed.scheme.lower() == "https" else 80
    if port != default_port:
        host_for_header = f"{host_for_header}:{port}"

    def _address_order(address: str) -> tuple[int, str]:
        parsed_address = ipaddress.ip_address(address)
        return (0 if parsed_address.version == 4 else 1, parsed_address.compressed)

    original = str(httpx.URL(value))
    return [
        _PinnedRemoteTarget(
            original_url=original,
            connect_url=str(httpx.URL(value).copy_with(host=address)),
            host_header=host_for_header,
            sni_hostname=ascii_host,
        )
        for address in sorted(addresses, key=_address_order)
    ]


async def request_public_https(
    url: str,
    *,
    method: str = "POST",
    content: bytes | str | None = None,
    headers: Mapping[str, str] | None = None,
    timeout_seconds: float = 10,
    timeout: httpx.Timeout | None = None,
    max_request_bytes: int = 256 * 1024,
    max_response_bytes: int = 64 * 1024,
) -> PublicHTTPResponse:
    """Send one bounded request to a public HTTPS endpoint without DNS rebinding.

    The hostname is resolved and validated once, then the TCP connection is made
    to that exact public IP while retaining the original Host header and TLS SNI.
    Redirects, proxy environment variables, credentials in URLs, private
    addresses and oversized bodies are rejected.
    """

    normalized_method = str(method or "").strip().upper()
    if normalized_method not in {"GET", "POST"}:
        raise UnsafeRemoteURLError("出站请求仅支持 GET 或 POST")
    request_bytes = (
        content.encode("utf-8") if isinstance(content, str) else bytes(content or b"")
    )
    if len(request_bytes) > max(1, int(max_request_bytes)):
        raise UnsafeRemoteURLError("出站请求内容过大")
    response_limit = max(1, min(int(max_response_bytes), 1024 * 1024))
    timeout_value = max(3.0, min(float(timeout_seconds), 60.0))
    targets = await asyncio.to_thread(
        _resolve_public_image_targets,
        url,
        require_https=True,
    )
    timeout = timeout or httpx.Timeout(timeout_value, connect=min(timeout_value, 5.0))
    last_connection_error: httpx.HTTPError | None = None

    for target in targets:
        outgoing_headers = httpx.Headers(headers or {})
        outgoing_headers["Host"] = target.host_header
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                async with client.stream(
                    normalized_method,
                    target.connect_url,
                    content=request_bytes if normalized_method == "POST" else None,
                    headers=outgoing_headers,
                    extensions={"sni_hostname": target.sni_hostname},
                ) as response:
                    if 300 <= response.status_code < 400:
                        raise UnsafeRemoteURLError("出站请求拒绝重定向")
                    raw_length = response.headers.get("content-length")
                    try:
                        declared_size = int(raw_length) if raw_length else None
                    except ValueError as exc:
                        raise UnsafeRemoteURLError("出站响应大小信息无效") from exc
                    if declared_size is not None and (
                        declared_size < 0 or declared_size > response_limit
                    ):
                        raise UnsafeRemoteURLError("出站响应内容过大")

                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes(16 * 1024):
                        total += len(chunk)
                        if total > response_limit:
                            raise UnsafeRemoteURLError("出站响应内容过大")
                        chunks.append(chunk)
                    return PublicHTTPResponse(
                        status_code=response.status_code,
                        headers=httpx.Headers(response.headers),
                        body=b"".join(chunks),
                    )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            last_connection_error = exc
            continue

    raise UnsafeRemoteURLError("公网 HTTPS 地址暂时无法连接") from last_connection_error


def validate_public_image_url(url: str) -> str:
    return _resolve_public_image_targets(url)[0].original_url


def validate_public_https_url_syntax(url: str) -> str:
    """Validate a configurable public HTTPS endpoint without DNS I/O.

    Save paths use this deterministic check for immediate feedback. Actual
    requests must still use ``request_public_https`` so DNS is validated and
    pinned at the connection boundary.
    """

    value = str(url or "").strip()
    if not value or len(value) > 2048:
        raise UnsafeRemoteURLError("公网 HTTPS 地址不能为空且不能超过 2048 个字符")
    parsed = urlparse(value)
    if parsed.scheme.casefold() != "https" or not parsed.hostname:
        raise UnsafeRemoteURLError("仅支持公网 HTTPS 地址")
    if parsed.username or parsed.password or parsed.fragment:
        raise UnsafeRemoteURLError("公网 HTTPS 地址格式不安全")
    host = parsed.hostname.rstrip(".")
    if host.casefold() == "localhost" or host.casefold().endswith(".localhost"):
        raise UnsafeRemoteURLError("不允许访问本机或内网地址")
    try:
        parsed.port
    except ValueError as exc:
        raise UnsafeRemoteURLError("公网 HTTPS 地址端口无效") from exc
    try:
        literal_ip = ipaddress.ip_address(host)
    except ValueError:
        literal_ip = None
    if literal_ip is not None and not _is_public_ip(literal_ip.compressed):
        raise UnsafeRemoteURLError("不允许访问本机或内网地址")
    try:
        return str(httpx.URL(value))
    except (httpx.InvalidURL, UnicodeError, ValueError) as exc:
        raise UnsafeRemoteURLError("公网 HTTPS 地址格式无效") from exc


def validate_public_https_url(url: str) -> str:
    """Backward-compatible strict HTTPS validator for security-sensitive callers."""
    return _resolve_public_image_targets(url, require_https=True)[0].original_url


async def download_public_image(
    url: str, max_bytes: int | None = None, *, timeout_seconds: float = 30,
    allow_redirects: bool = True,
) -> bytes:
    targets = await asyncio.to_thread(
        _resolve_public_image_targets,
        url,
        require_https=True,
    )
    limit = int(max_bytes or settings.max_upload_bytes)
    timeout_value = max(3.0, min(float(timeout_seconds), 60.0))
    timeout = httpx.Timeout(timeout_value, connect=min(timeout_value, 5.0))
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as client:
        for redirect_count in range(4):
            redirect_url: str | None = None
            connection_error: httpx.HTTPError | None = None
            for target in targets:
                try:
                    async with client.stream(
                        "GET",
                        target.connect_url,
                        headers={
                            "Host": target.host_header,
                            "User-Agent": "xianyu-assistant/1.0",
                        },
                        extensions={"sni_hostname": target.sni_hostname},
                    ) as response:
                        if 300 <= response.status_code < 400:
                            if not allow_redirects:
                                raise UnsafeRemoteURLError("商业图片回源拒绝重定向")
                            location = response.headers.get("location")
                            if not location or redirect_count >= 3:
                                raise UnsafeRemoteURLError("远程图片重定向次数过多或地址无效")
                            redirect_url = urljoin(target.original_url, location)
                            break

                        response.raise_for_status()
                        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                        if content_type == "image/jpg":
                            content_type = "image/jpeg"
                        if content_type not in SAFE_IMAGE_MEDIA_TYPES:
                            raise UploadValidationError("远程地址返回的不是受支持的栅格图片")
                        content_length = response.headers.get("content-length")
                        try:
                            declared_size = int(content_length) if content_length else None
                        except ValueError as exc:
                            raise UploadValidationError("远程图片大小信息无效") from exc
                        if declared_size is not None and (declared_size <= 0 or declared_size > limit):
                            raise UploadValidationError(f"图片不能超过 {limit // 1024 // 1024}MB")
                        chunks: list[bytes] = []
                        size = 0
                        async for chunk in response.aiter_bytes(64 * 1024):
                            size += len(chunk)
                            if size > limit:
                                raise UploadValidationError(f"图片不能超过 {limit // 1024 // 1024}MB")
                            chunks.append(chunk)
                        data = b"".join(chunks)
                        _, detected_type = await asyncio.to_thread(
                            validate_image_bytes,
                            data,
                        )
                        if detected_type != content_type:
                            raise UploadValidationError("远程图片声明类型与实际内容不一致")
                        return data
                except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                    connection_error = exc
                    continue

            if redirect_url is not None:
                targets = await asyncio.to_thread(
                    _resolve_public_image_targets,
                    redirect_url,
                    require_https=True,
                )
                continue
            if connection_error is not None:
                raise UploadValidationError("远程图片服务连接失败") from connection_error
            raise UploadValidationError("远程图片下载失败")
        else:  # pragma: no cover - loop exits via break or explicit redirect error
            raise UnsafeRemoteURLError("远程图片重定向次数过多")
