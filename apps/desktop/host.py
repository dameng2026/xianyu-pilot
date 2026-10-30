"""Run with: python -m apps.desktop.host (from the repository root)."""
import argparse
from pathlib import Path
import socket
import sys
import threading
import urllib.error
import urllib.request
import webbrowser

# Explicit source roots, before importing any reusable API modules.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from .app_factory import create_app
from .runtime import InstanceLock, default_data_dir
from .storage import Store


def open_when_ready(url: str, stop: threading.Event):
    # Local readiness only: ignore system proxy settings and forbid redirects.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    for _ in range(80):
        if stop.is_set():
            return
        try:
            with opener.open(url + "/health", timeout=0.5) as response:
                if response.status == 200 and not stop.is_set():
                    webbrowser.open(url)
                    return
        except (OSError, urllib.error.URLError):
            pass
        if stop.wait(0.15):
            return


def main() -> int:
    parser = argparse.ArgumentParser(description="无需 Docker / MySQL / Redis 的本地工作流")
    parser.add_argument("--port", type=int, default=15179)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    data_dir = default_data_dir()
    if not 1024 <= args.port <= 65535:
        parser.error("port 必须在 1024–65535 之间")
    web_dir = Path(__file__).parent / "web-dist"
    if not (web_dir / "index.html").is_file():
        print("前端尚未构建，请运行 start-desktop-demo.bat。", file=sys.stderr)
        return 1
    try:
        with InstanceLock(data_dir), socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            # Reserve before migration; an occupied port never changes user data.
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(("127.0.0.1", args.port))
            listener.listen(128)
            store = Store(data_dir / "demo.sqlite3")
            store.migrate()
            app = create_app(store, port=args.port, web_dir=web_dir,
                             on_shutdown=lambda: setattr(server, "should_exit", True))
            url = f"http://127.0.0.1:{args.port}"
            print(f"\n本地工作流：{url}\n数据目录：{data_dir}\nMCP：{url}/mcp\n真实 QQ 能力需由用户自行配置并验证；页面可停止服务。\n", flush=True)
            stop = threading.Event()
            if not args.no_browser:
                threading.Thread(target=open_when_ready, args=(url, stop), daemon=True).start()
            try:
                import uvicorn
                server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False, proxy_headers=False))
                server.run(sockets=[listener])
            finally:
                stop.set()
        return 0
    except KeyboardInterrupt:
        return 0
    except (OSError, RuntimeError) as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
