"""Generic repository — the only layer that talks to SQLAlchemy.

Services depend on repositories, never on the ORM directly. This keeps business
logic testable with fakes and preserves the option of splitting a module into
its own microservice (the repository interface becomes the client boundary).
Soft-deleted rows are excluded by default.
"""
import uuid
from typing import Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.db.base import Base, SoftDeleteMixin

ModelT = TypeVar("ModelT", bound=Base)


# Kept as an explicit TypeVar + Generic rather than PEP 695 syntax: every
# subclass across the modules names ModelT directly, and mypy's strict mode is
# better behaved with the classic spelling here.
class BaseRepository(Generic[ModelT]):  # noqa: UP046
    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _base_query(self):
        stmt = select(self.model)
        if issubclass(self.model, SoftDeleteMixin):
            stmt = stmt.where(self.model.deleted_at.is_(None))
        return stmt

    async def get(self, entity_id: uuid.UUID) -> ModelT:
        stmt = self._base_query().where(self.model.id == entity_id)
        result = await self.session.execute(stmt)
        entity = result.scalar_one_or_none()
        if entity is None:
            raise NotFoundError(
                f"{self.model.__name__} not found", details={"id": str(entity_id)}
            )
        return entity

    async def list(self, *, limit: int = 50, offset: int = 0) -> list[ModelT]:
        stmt = self._base_query().limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars())

    def add(self, entity: ModelT) -> ModelT:
        self.session.add(entity)
        return entity

    async def flush(self) -> None:
        await self.session.flush()
