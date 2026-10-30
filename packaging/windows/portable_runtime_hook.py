"""PyInstaller runs user hooks before Qt's bundled runtime hooks."""
from app.portable.bootstrap import configure_environment
from app.core.portable_runtime import startup_error
try:
    configure_environment()
except (OSError,ValueError) as exc:
    startup_error(exc)
