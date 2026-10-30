"""Windows directory-style EXE entry. No browser or service before data checks."""
import argparse
import multiprocessing
from pathlib import Path
import sys

API_ROOT=Path(__file__).resolve().parent
if str(API_ROOT) not in sys.path:
    sys.path.insert(0,str(API_ROOT))
sys.dont_write_bytecode=True


def main():
    parser=argparse.ArgumentParser(description="闲鱼助手 Windows 绿色便携版")
    parser.add_argument("--port",type=int,default=15177)
    parser.add_argument("--self-check",action="store_true")
    parser.add_argument("--smoke-test",action="store_true")
    parser.add_argument("--webengine-check",action="store_true")
    args=parser.parse_args()
    if not 1024<=args.port<=65535:
        parser.error("端口须为 1024–65535")
    from app.core.portable_runtime import startup_error
    from app.portable.bootstrap import configure_environment,bundled_web_dir,configure_logging,portable_self_check
    handler=None
    backend=None
    try:
        root,paths=configure_environment()
        handler=configure_logging(root)
        web=bundled_web_dir()
        if args.self_check:
            portable_self_check(root,web)
            return 0
        if args.webengine_check:
            from app.portable.webengine_check import check
            return check(root,paths,web,args.port)
        if sys.platform!="win32":
            raise RuntimeError("桌面宿主仅支持 Windows")
        if not (web/"desktop-safety.html").is_file():
            raise RuntimeError("缺少内嵌前端，请使用完整便携包")
        from app.core.instance_lock import SingleInstance
        # Hold before importing Chromium or starting threads. Backend borrows it.
        with SingleInstance(root) as instance:
            from app.core.config import Settings
            from app.portable.backend import DesktopBackend
            from app.portable.qt_host import DesktopConsole
            backend=DesktopBackend(Settings(runtime_mode="desktop",desktop_port=args.port),web,instance)
            console=DesktopConsole(backend,paths,smoke=args.smoke_test)
            try:
                return console.run()
            finally:
                backend.stop()
                if backend.thread:
                    backend.thread.join()  # No force-exit while SQLite commits.
                console.dispose()
    except (PermissionError,OSError,ValueError) as exc:
        startup_error(exc)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).error("Portable startup failed: %s",type(exc).__name__)
        if sys.platform=="win32" and not args.self_check and not args.smoke_test and not args.webengine_check:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,"启动失败："+str(exc)+"。请保留 data/，检查 logs 后重试。","闲鱼助手",0x10)
        return 1
    finally:
        if handler:
            import logging
            logging.getLogger().removeHandler(handler)
            handler.close()


if __name__=="__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
