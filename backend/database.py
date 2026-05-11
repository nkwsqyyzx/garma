"""SQLAlchemy async MySQL 连接管理。"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from backend.config import get_settings

engine = None
async_session_factory = None


class Base(DeclarativeBase):
    pass


async def init_db() -> None:
    """在 startup 时调用，创建引擎和 session 工厂。"""
    global engine, async_session_factory
    settings = get_settings()
    engine = create_async_engine(
        settings.DATABASE_URL,
        pool_size=10,
        max_overflow=20,
        pool_recycle=3600,
        echo=False,
    )
    async_session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )


async def get_db():
    """FastAPI 依赖注入生成器。"""
    if async_session_factory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def close_db() -> None:
    """在 shutdown 时调用，关闭引擎。"""
    global engine
    if engine:
        await engine.dispose()
        engine = None
