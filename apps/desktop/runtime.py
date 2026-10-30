from pathlib import Path
from app.core.instance_lock import SingleInstance
from app.core.paths import data_directory


def default_data_dir() -> Path:
    """Keep historical demo data beside the program, separate from real data."""
    return data_directory() / "demo"


class InstanceLock(SingleInstance):
    """Compatibility wrapper for the historical demo's separate data folder."""
    def __init__(self, directory: Path):
        super().__init__(directory, filename="host.lock", description="演示服务")
