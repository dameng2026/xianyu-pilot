"""A per-data-directory OS lock, released automatically when the host exits."""
import os
from pathlib import Path
from app.core.paths import contained_data_path


class SingleInstance:
    def __init__(self, directory: Path, *, filename="app.lock", description="应用"):
        self.path = Path(directory) / filename
        self.description = description
        self.file = None

    def __enter__(self):
        contained_data_path(self.path.parent, self.path.name)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                if self.path.stat().st_size == 0:
                    self.file.write(b"\0")
                    self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            self.file = None
            raise RuntimeError(f"本数据目录已有{self.description}运行，请先停止原实例") from None
        return self

    def __exit__(self, *_):
        if self.file is not None:
            self.file.close()
            self.file = None
