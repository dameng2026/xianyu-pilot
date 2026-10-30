"""Assemble and verify the original application's native source distribution."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tarfile
import zipfile

if __package__:
    from . import artifacts
else:
    import artifacts

ROOT = Path(__file__).resolve().parents[2]
INFO = "package-info.json"
ROOT_FILES = {
    "README.md", "LICENSE", "NOTICE", ".env.example", ".env.development.example",
    "start-local.bat", "start-local.sh", "stop-local.bat", "stop-local.sh",
    "status-local.bat", "status-local.sh", "verify-local.bat",
    "start-desktop.bat", "start-desktop.sh",
}
LOCAL_SCRIPTS = {
    "scripts/local-dev.ps1", "scripts/setup-local.ps1", "scripts/setup-local.sh",
    "scripts/verify-local.ps1", "scripts/read_only_smoke.py",
    "scripts/build-windows-portable.ps1", "scripts/ci/windows_portable.py", "scripts/ci/windows_portable_smoke.py",
}
LOCAL_DOCS = {"docs/local-dev-runbook.md", "docs/native-deployment.md", "docs/desktop-sqlite-foundation.md", "docs/desktop-delivery-design.md", "docs/official-order-delivery.md", "docs/delivery-safety-recovery.md", "docs/windows-portable.md"}
PORTABLE_BUILD_FILES={"packaging/windows/requirements-build.txt","packaging/windows/xianyu_portable.spec","packaging/windows/portable_runtime_hook.py"}
REQUIRED = {
    "LICENSE", "start-local.bat", "start-local.sh", "scripts/setup-local.ps1",
    "scripts/setup-local.sh", "scripts/local-dev.ps1", "apps/api/app/main.py",
    "apps/api/requirements.txt", "apps/web/package-lock.json", "apps/crawler/package-lock.json",
    "apps/web/dist/index.html", "apps/crawler/dist/server.js", "docs/native-deployment.md",
    "NATIVE-README.txt",
    "start-desktop.bat", "start-desktop.sh", "apps/api/run_desktop.py",
    "apps/api/app/desktop_migrations/001_original_core.sql", "docs/desktop-sqlite-foundation.md",
    "apps/api/app/desktop_migrations/002_inventory_rules.sql", "apps/api/app/desktop_inventory_api.py",
    "apps/api/app/services/desktop_inventory.py",
    "apps/api/app/desktop_migrations/003_delivery_state.sql", "apps/api/app/desktop_delivery_api.py",
    "apps/api/app/services/desktop_delivery.py", "apps/api/app/services/desktop_delivery_contracts.py",
    "apps/api/app/core/portable_runtime.py", "docs/desktop-delivery-design.md",
    "apps/api/app/desktop_migrations/004_official_delivery.sql", "docs/official-order-delivery.md",
    "apps/api/app/services/official_delivery_monitor.py", "apps/api/app/services/official_delivery_gateway.py",
    "apps/api/app/services/official_delivery_store.py", "apps/api/app/services/official_delivery_contracts.py",
    "apps/api/app/services/unified_delivery_rules.py", "apps/api/app/services/seller_orders_api.py",
    "apps/api/app/desktop_migrations/005_delivery_safety.sql", "apps/api/app/desktop_safety_api.py",
    "apps/api/app/services/delivery_safety.py", "apps/api/app/services/delivery_resume.py",
    "docs/delivery-safety-recovery.md", "apps/web/dist/desktop-safety.html",
    "apps/api/run_portable.py", "apps/api/app/portable/backend.py", "apps/api/app/portable/bootstrap.py",
    "apps/api/app/portable/qt_host.py", "apps/api/app/portable/windows_events.py", "apps/api/app/portable/webengine_check.py",
    "apps/api/app/services/delivery_gate.py", "docs/windows-portable.md", "scripts/build-windows-portable.ps1",
    *PORTABLE_BUILD_FILES,
}
README = """Xianyu Pilot - original system, native source distribution

This is a source deployment ZIP with tested Web/Crawler builds, not a standalone EXE.
The application and this distribution require no container runtime.

Desktop SQLite inventory/rules: read docs/desktop-sqlite-foundation.md and run
start-desktop.bat (Windows) or sh start-desktop.sh. Python dependencies are needed.
This phase provides warehouse/rule APIs and read-only original order/delivery
records. The local segmented state machine runs in offline Mock tests only.
Build Web to use /desktop/safety for local delivery-state review. Actual delivery
requires explicitly configured and verified official adapters; it stays off by default.
It needs no MySQL/Redis and sends no platform messages. See the delivery design.

Original server development mode (pending SQLite business migration):
Prepare Python 3.11, Node.js 22.23.1 / npm 10.9.8, local MySQL 8 and Redis 7.
First-time dependency and Playwright browser installation needs network access.
Read docs/native-deployment.md before running start-local.bat or sh start-local.sh.
The original scripts initialize local settings and a database; use a fresh target
directory and back up existing data before changing an existing installation.

Default Web: http://127.0.0.1:15176
Credentials and business data are not included. Keep .env and local data private.
"""


def metadata(env: dict) -> dict:
    result = artifacts.identity("web", env)
    result["component"] = "native-source"
    return result


def safe_name(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(name and not path.is_absolute() and ".." not in path.parts
                and path.as_posix() == name and "\\" not in name and ":" not in name)


def allowed_source(name: str) -> bool:
    if not safe_name(name):
        return False
    path = PurePosixPath(name)
    if name in ROOT_FILES or name in LOCAL_SCRIPTS or name in LOCAL_DOCS or name in PORTABLE_BUILD_FILES:
        return True
    if path.name.startswith(".env") or path.name in {"Dockerfile", ".dockerignore"}:
        return False
    if any(part in {"node_modules", "dist", "__pycache__", ".git", "tests", "test"} for part in path.parts):
        return False
    if name.startswith(("apps/api/uploads/", "apps/api/secrets/", "apps/api/screenshots/", "apps/crawler/screenshots/")):
        return False
    return name.startswith(("apps/api/", "apps/web/", "apps/crawler/"))


def allowed_payload(name: str) -> bool:
    if name == "NATIVE-README.txt" or allowed_source(name):
        return True
    for prefix in ("apps/web/dist/", "apps/crawler/dist/"):
        if name.startswith(prefix):
            relative = name.removeprefix(prefix)
            return safe_name(relative) and not any(part.startswith(".env") or part in {".git", "node_modules", "secrets"}
                                                   for part in PurePosixPath(relative).parts)
    return False


def committed_sources(env: dict) -> dict[str, bytes]:
    sha = metadata(env)["revision"]
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()
    if head != sha:
        raise ValueError("Checkout does not match the requested source revision")
    raw = subprocess.run(["git", "archive", "--format=tar", sha], cwd=ROOT, check=True, capture_output=True).stdout
    files = {}
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        for item in archive:
            if item.isdir() or not allowed_source(item.name):
                continue
            if not item.isfile():
                raise ValueError("Native source package must not contain links")
            with archive.extractfile(item) as content:
                files[item.name] = content.read()
    return files


def create(directory: Path, web_dist: Path, crawler_dist: Path, env: dict, sources=None) -> Path:
    info = metadata(env)
    files = dict(committed_sources(env) if sources is None else sources)
    if any(not allowed_source(name) for name in files):
        raise ValueError("Source package contains an unexpected or private path")
    for component, dist in [("web", web_dist), ("crawler", crawler_dist)]:
        manifest = artifacts.verify(dist, component, env)
        for name in [*manifest["files"], artifacts.MANIFEST]:
            files[f"apps/{component}/dist/{name}"] = (dist / name).read_bytes()
    files["NATIVE-README.txt"] = README.encode("utf-8")
    if not REQUIRED <= files.keys():
        raise ValueError("Native source distribution is incomplete")
    info["files"] = {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())}
    files[INFO] = (json.dumps(info, sort_keys=True, indent=2) + "\n").encode("utf-8")
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / f"xianyu-pilot-native-{info['revision'][:12]}.zip"
    if directory.is_symlink() or output.is_symlink() or (directory / "SHA256SUMS.txt").is_symlink():
        raise ValueError("Native output paths must not be symbolic links")
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            if not safe_name(name):
                raise ValueError("Unsafe native package path")
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = (0o100755 if name.endswith(".sh") else 0o100644) << 16
            archive.writestr(entry, data)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    (directory / "SHA256SUMS.txt").write_text(f"{digest}  {output.name}\n", encoding="utf-8")
    return output


def verify(directory: Path, env: dict) -> tuple[Path, dict]:
    expected = metadata(env)
    filename = f"xianyu-pilot-native-{expected['revision'][:12]}.zip"
    archive_path = directory / filename
    if directory.is_symlink() or archive_path.is_symlink() or (directory / "SHA256SUMS.txt").is_symlink():
        raise ValueError("Release package paths must not be symbolic links")
    checksum = (directory / "SHA256SUMS.txt").read_text(encoding="utf-8")
    actual = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    if checksum != f"{actual}  {filename}\n":
        raise ValueError("Native ZIP checksum mismatch")
    with zipfile.ZipFile(archive_path) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if len(names) != len(set(name.casefold() for name in names)) or any(not safe_name(name) for name in names):
            raise ValueError("Unsafe or duplicate package paths")
        if any(stat.S_ISLNK(entry.external_attr >> 16) or entry.file_size > 50_000_000 for entry in entries):
            raise ValueError("Unsupported native package entry")
        if sum(entry.file_size for entry in entries) > 300_000_000:
            raise ValueError("Native package is too large")
        info = json.loads(archive.read(INFO))
        if not isinstance(info, dict) or set(info) != set(expected) | {"files"}:
            raise ValueError("Invalid native package metadata")
        if any(type(info[key]) is not type(value) or info[key] != value for key, value in expected.items()):
            raise ValueError("Native package belongs to another revision or run")
        files = info["files"]
        if not isinstance(files, dict) or not REQUIRED <= files.keys() or set(names) != set(files) | {INFO}:
            raise ValueError("Native file inventory is incomplete")
        for name, digest in files.items():
            if (not allowed_payload(name) or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                    or hashlib.sha256(archive.read(name)).hexdigest() != digest):
                raise ValueError("Native package file checksum mismatch")
    return archive_path, info


def release_target(env: dict) -> tuple[str, bool]:
    metadata(env)
    ref = env.get("GITHUB_REF", "")
    if env.get("GITHUB_EVENT_NAME") not in {"push", "workflow_dispatch"}:
        raise ValueError("Only trusted pushes or manual runs can publish")
    if ref.startswith("refs/tags/"):
        tag = ref.removeprefix("refs/tags/")
        if re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9][A-Za-z0-9.-]*)?", tag):
            return tag, "-" in tag
    elif ref == "refs/heads/main" and env["GITHUB_EVENT_NAME"] == "workflow_dispatch":
        attempt = env.get("GITHUB_RUN_ATTEMPT", "")
        if re.fullmatch(r"[1-9][0-9]*", attempt):
            return f"native-{env['GITHUB_RUN_ID']}-{attempt}", True
    raise ValueError("Publication is restricted to version tags or manual main snapshots")


def publish(directory: Path, env: dict):
    tag, prerelease = release_target(env)
    path, info = verify(directory, env)
    repo = env.get("GH_REPO", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("Invalid release repository")
    if env["GITHUB_REF"].startswith("refs/tags/"):
        current = subprocess.run(["gh", "api", f"repos/{repo}/commits/{tag}", "--jq", ".sha"],
                                 check=True, capture_output=True, text=True).stdout.strip()
        if current != info["revision"]:
            raise ValueError("Version tag no longer points to the verified revision")
    notes = directory / "release-notes.md"
    notes.write_text(
        "原系统本机部署源码包，包含已验证的 Web/Crawler 构建、后端源码与本机启动脚本。\n\n"
        "无需 Docker；需要本机 Python 3.11、Node.js 22.23.1/npm 10.9.8、MySQL 8 与 Redis 7。"
        "这不是独立 EXE，首次依赖与浏览器安装需要联网。请先阅读包内 NATIVE-README.txt。\n\n"
        f"Source revision: {info['revision']}\nBuild run: {info['run']}\n", encoding="utf-8")
    command = ["gh", "release", "create", tag, str(path), str(directory / "SHA256SUMS.txt"),
               "--repo", repo, "--target", info["revision"], "--title", f"本机部署源码包 · {tag}",
               "--notes-file", str(notes)]
    if prerelease:
        command += ["--prerelease", "--latest=false"]
    else:
        command += ["--verify-tag"]
    # An existing release is not overwritten on rerun.
    subprocess.run(command, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("create", "verify", "summary", "publish"))
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--web-dist", type=Path)
    parser.add_argument("--crawler-dist", type=Path)
    args = parser.parse_args()
    if args.command == "create":
        if args.web_dist is None or args.crawler_dist is None:
            parser.error("create requires both tested compiled artifacts")
        create(args.directory, args.web_dist, args.crawler_dist, os.environ)
    elif args.command == "publish":
        publish(args.directory, os.environ)
    else:
        path, info = verify(args.directory, os.environ)
        print(f"Verified {path.name}: {len(info['files'])} files at {info['revision']}")
        if args.command == "summary":
            url = os.environ.get("ARTIFACT_URL", "")
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
                stream.write(f"## Native source distribution\n\n[Download verified ZIP]({url})\n\n"
                             f"Source: `{info['revision']}`\n\n"
                             "Requires local Python, Node.js, MySQL and Redis. Not a standalone EXE.\n")


if __name__ == "__main__":
    main()
