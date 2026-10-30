import logging
from sqlalchemy import event
from sqlalchemy.engine import make_url

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from .config import settings

logger = logging.getLogger(__name__)

def create_configured_engine(database_url, config=None):
    """Create an engine without ever rendering its credential-bearing URL."""

    config = config or settings
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite":
        database_engine = create_async_engine(url, connect_args={"timeout": 5}, pool_pre_ping=True)

        @event.listens_for(database_engine.sync_engine, "connect")
        def configure_sqlite(connection, _record):
            cursor = connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA busy_timeout=5000")
                cursor.execute("PRAGMA synchronous=FULL")
                cursor.execute("PRAGMA temp_store=MEMORY")
            finally:
                cursor.close()

        return database_engine
    if url.get_backend_name() != "mysql":
        raise ValueError("Unsupported application storage backend")
    return create_async_engine(
        database_url,
        echo=False,
        pool_size=config.db_pool_size,
        max_overflow=config.db_max_overflow,
        pool_timeout=config.db_pool_timeout_seconds,
        pool_recycle=config.db_pool_recycle_seconds,
        pool_pre_ping=True,
        connect_args={
            "connect_timeout": config.mysql_connect_timeout_seconds,
            # aiomysql does not expose client-side socket read/write timeout
            # arguments. Configure the equivalent MySQL session safeguards on
            # every new connection instead.
            "init_command": (
                "SET SESSION net_read_timeout="
                f"{config.mysql_read_timeout_seconds}, "
                "SESSION net_write_timeout="
                f"{config.mysql_write_timeout_seconds}"
            ),
        },
    )


class DatabaseRuntime:
    """Select storage explicitly and create connections only when first used."""

    def __init__(self, config):
        self.config = config
        self._engine = None
        self._sessions = None

    @property
    def engine(self):
        if self._engine is None:
            self._engine = create_configured_engine(self.config.database_url, self.config)
        return self._engine

    def session(self, **kwargs):
        if self._sessions is None:
            self._sessions = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)
        return self._sessions(**kwargs)

    async def dispose(self):
        if self._engine is not None:
            await self._engine.dispose()
        self._engine = self._sessions = None


database = DatabaseRuntime(settings)
DATABASE_URL = settings.database_url


class LazyEngine:
    def __getattr__(self, name):
        return getattr(database.engine, name)

    async def dispose(self):
        await database.dispose()


engine = LazyEngine()

def async_session(**kwargs):
    return database.session(**kwargs)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with async_session() as session:
        try:
            yield session
        except BaseException:
            try:
                await session.rollback()
            except Exception:
                logger.warning("Database rollback failed", exc_info=True)
            raise
        finally:
            await session.close()
