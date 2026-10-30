"""Application factory; desktop startup never imports server-only services."""
from app.core.config import settings


def create_app():
    if settings.is_desktop:
        from app.desktop_app import create_desktop_app
        return create_desktop_app(settings)
    # Transitional entry for original routes not yet adapted to SQLite.
    from app.server_app import app as original_app
    return original_app


app = create_app()
