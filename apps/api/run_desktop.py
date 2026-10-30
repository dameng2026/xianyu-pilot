"""Start the original API's SQLite adaptation without external services."""
import argparse
import os
from pathlib import Path
import sys

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))


def main():
    parser = argparse.ArgumentParser(description="闲鱼助手 SQLite 存储基础（业务接口适配中）")
    parser.add_argument("--port", type=int, default=15177)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("端口须为 1024–65535")
    os.environ["RUNTIME_MODE"] = "desktop"
    os.environ["DESKTOP_PORT"] = str(args.port)
    from app.core.portable_runtime import prepare_portable_runtime, startup_error
    try:
        prepare_portable_runtime()
    except (OSError, ValueError) as exc:
        startup_error(exc)
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=args.port, workers=1, reload=False, app_dir=str(CURRENT_DIR))


if __name__ == "__main__":
    main()
