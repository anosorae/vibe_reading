"""数据库连接与会话管理 (SQLite + aiosqlite 异步)。"""
from pathlib import Path
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

BASE_DIR = Path(__file__).parent
DATABASE_PATH = BASE_DIR / "vibe_reading.db"
DATABASE_URL = f"sqlite+aiosqlite:///{DATABASE_PATH.as_posix()}"

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    future=True,
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine.sync_engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
    """SQLite 默认关闭外键约束, 这里打开后 cascade='all, delete-orphan' 才能生效。

    同时开启 WAL 模式: 读写并发更友好, 大量 INSERT 速度提升 3-5x。
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.execute("PRAGMA journal_mode = WAL")
    cursor.close()


async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """SQLAlchemy 2.0 声明式基类。"""
    pass


async def get_session() -> AsyncSession:
    """FastAPI 依赖:每次请求产出一个 AsyncSession,请求结束自动关闭。"""
    async with async_session_maker() as session:
        yield session


async def init_db() -> None:
    """启动时建表 (首次运行会自动创建 vibe_reading.db)。"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
