"""Download pinned CI tools and verify the vendor's exact asset checksum."""

from __future__ import annotations

import argparse
import hashlib
import io
from pathlib import Path
import platform
import tarfile
from urllib.request import urlopen
import zipfile


TOOLS = {
    "actionlint": ("rhysd/actionlint", "1.7.12"),
    "gitleaks": ("gitleaks/gitleaks", "8.24.2"),
}


def install(name: str, destination: Path) -> Path:
    repo, version = TOOLS[name]
    windows = platform.system() == "Windows"
    if platform.machine().lower() not in {"amd64", "x86_64"}:
        raise ValueError("CI tool installer currently supports x64 runners only")
    system = "windows" if windows else "linux"
    arch = "x64" if name == "gitleaks" else "amd64"
    suffix = "zip" if windows else "tar.gz"
    asset = f"{name}_{version}_{system}_{arch}.{suffix}"
    base = f"https://github.com/{repo}/releases/download/v{version}"
    with urlopen(f"{base}/{name}_{version}_checksums.txt", timeout=60) as response:
        checksums = response.read().decode("utf-8")
    entries = [line.split() for line in checksums.splitlines()]
    expected = [parts[0] for parts in entries if len(parts) >= 2 and parts[1].lstrip("*").lstrip("./") == asset]
    if len(expected) != 1:
        raise ValueError("Missing or ambiguous vendor checksum")
    with urlopen(f"{base}/{asset}", timeout=60) as response:
        archive = response.read()
    if hashlib.sha256(archive).hexdigest() != expected[0]:
        raise ValueError("CI tool checksum mismatch")
    executable = name + (".exe" if windows else "")
    if windows:
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            names = [n for n in bundle.namelist() if Path(n).name.lower() == executable.lower()]
            if not names:
                raise ValueError("CI tool binary is missing")
            binary = bundle.read(names[0])
    else:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
            members = [m for m in bundle.getmembers() if Path(m.name).name == executable and m.isreg()]
            if not members:
                raise ValueError("CI tool binary is missing")
            member = bundle.extractfile(members[0])
            if member is None:
                raise ValueError("CI tool binary is missing")
            binary = member.read()
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / executable
    path.write_bytes(binary)
    path.chmod(0o755)
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tool", choices=TOOLS)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(install(args.tool, args.destination))
