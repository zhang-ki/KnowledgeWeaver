from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from .schema import Base

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


class Database:
    """Small async SQLAlchemy boundary; accepts a PostgreSQL psycopg URL."""

    def __init__(self, url: str, *, echo: bool = False) -> None:
        if not url.startswith(("postgresql+psycopg://", "postgresql://")):
            raise ValueError("B03 requires a PostgreSQL connection URL")
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        self.engine: AsyncEngine = create_async_engine(url, echo=echo, pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[Any]:
        async with self.sessions() as session:
            yield session

    async def create_schema_for_tests(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def dispose(self) -> None:
        await self.engine.dispose()
