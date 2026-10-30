"""Bind compiled web/crawler files to a source revision and Actions run."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re

MANIFEST = "ci-build-info.json"
ENTRYPOINTS = {"web": "index.html", "crawler": "server.js"}


def identity(component: str, env: dict) -> dict:
    if component not in ENTRYPOINTS:
        raise ValueError("Unknown compiled component")
    sha = env.get("GITHUB_SHA", "")
    run = env.get("GITHUB_RUN_ID", "")
    if not re.fullmatch(r"[0-9a-f]{40}", sha) or not re.fullmatch(r"[1-9][0-9]*", run):
        raise ValueError("A full source revision and Actions run are required")
    # Reruns keep the run ID. Artifacts from successful, unrepeated jobs remain valid.
    return {"schema": 1, "component": component, "revision": sha, "run": run}


def checksums(directory: Path) -> dict[str, str]:
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError("Compiled artifact directory is missing or linked")
    files = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError("Compiled artifacts must not contain symbolic links")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("Unsupported artifact entry")
        relative = path.relative_to(directory).as_posix()
        if path.name.startswith(".env") or any(part in {".git", "node_modules", "secrets"} for part in path.relative_to(directory).parts):
            raise ValueError("Compiled artifacts contain an unexpected private path")
        if relative != MANIFEST:
            files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def record(directory: Path, component: str, env: dict) -> dict:
    metadata = identity(component, env)
    files = checksums(directory)
    if ENTRYPOINTS[component] not in files:
        raise ValueError("Compiled entrypoint is missing")
    metadata["files"] = files
    (directory / MANIFEST).write_text(json.dumps(metadata, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return metadata


def verify(directory: Path, component: str, env: dict) -> dict:
    manifest = directory / MANIFEST
    if manifest.is_symlink():
        raise ValueError("Artifact manifest must not be linked")
    metadata = json.loads(manifest.read_text(encoding="utf-8"))
    expected = identity(component, env)
    if not isinstance(metadata, dict) or set(metadata) != set(expected) | {"files"}:
        raise ValueError("Invalid artifact manifest")
    if any(type(metadata[key]) is not type(value) or metadata[key] != value for key, value in expected.items()):
        raise ValueError("Artifact belongs to a different component, revision or run")
    files = metadata["files"]
    if not isinstance(files, dict) or ENTRYPOINTS[component] not in files:
        raise ValueError("Artifact file inventory is missing")
    for name, digest in files.items():
        path = PurePosixPath(name)
        if (not isinstance(name, str) or path.is_absolute() or ".." in path.parts
                or path.as_posix() != name or "\\" in name or ":" in name
                or name == MANIFEST or not isinstance(digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise ValueError("Invalid artifact inventory entry")
    if files != checksums(directory):
        raise ValueError("Artifact contents differ from the tested build")
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("record", "verify"))
    parser.add_argument("--component", choices=ENTRYPOINTS, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    operation = record if args.command == "record" else verify
    metadata = operation(args.directory, args.component, os.environ)
    print(f"{args.component}: {len(metadata['files'])} compiled files {args.command}ed for {metadata['revision']}")


if __name__ == "__main__":
    main()
