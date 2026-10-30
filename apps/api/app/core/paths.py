"""Portable runtime paths anchored to the program, never the working directory."""
from pathlib import Path, PurePosixPath
import sys
import stat


API_DIR = Path(__file__).resolve().parents[2]
SOURCE_ROOT = API_DIR.parent.parent if API_DIR.parent.name.casefold() == "apps" else API_DIR


def program_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return SOURCE_ROOT


def data_directory() -> Path:
    return program_root() / "data"


def is_link_or_junction(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        # Python 3.11 has no Path.is_junction(). The native reparse attribute
        # still detects directory junctions and cloud reparse points there.
        attributes=getattr(path.lstat(),"st_file_attributes",0)
    except FileNotFoundError:
        return False
    return bool(attributes & getattr(stat,"FILE_ATTRIBUTE_REPARSE_POINT",0x400))


def contained_data_path(root: Path, relative: str) -> Path:
    """Resolve a portable reference, refusing traversal and existing links."""
    path = PurePosixPath(relative)
    if not relative or "\\" in relative or ":" in relative or path.is_absolute() or ".." in path.parts:
        raise ValueError("数据引用必须是 data 内的相对路径")
    root = Path(root).absolute()
    current = root
    for part in ("", *path.parts):
        current = current / part if part else current
        if is_link_or_junction(current):
            raise ValueError("数据目录不允许符号链接或目录联接")
    target = root.joinpath(*path.parts)
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("数据引用超出 data 目录")
    return target
