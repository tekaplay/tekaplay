import uuid

from sqlalchemy import func, select

from app.modules.content.models import (
    Campaign,
    Certification,
    ContentProject,
    ContentVersion,
    Course,
    Mission,
)
from app.repositories.base import BaseRepository


class CertificationRepository(BaseRepository[Certification]):
    model = Certification

    async def ordered(self) -> list[Certification]:
        stmt = self._base_query().order_by(Certification.sort_order,
                                           Certification.title)
        return list((await self.session.execute(stmt)).scalars())


class CampaignRepository(BaseRepository[Campaign]):
    model = Campaign

    async def ordered(self) -> list[Campaign]:
        stmt = self._base_query().order_by(Campaign.sort_order, Campaign.title)
        return list((await self.session.execute(stmt)).scalars())


class CourseRepository(BaseRepository[Course]):
    model = Course

    async def ordered(self, *, include_inactive: bool = False) -> list[Course]:
        stmt = self._base_query()
        if not include_inactive:
            stmt = stmt.where(Course.is_active.is_(True))
        stmt = stmt.order_by(Course.sort_order, Course.title)
        return list((await self.session.execute(stmt)).scalars())


class MissionRepository(BaseRepository[Mission]):
    model = Mission

    async def ordered(self) -> list[Mission]:
        stmt = self._base_query().order_by(Mission.sort_order, Mission.title)
        return list((await self.session.execute(stmt)).scalars())


class ContentProjectRepository(BaseRepository[ContentProject]):
    model = ContentProject

    async def get_by_slug(self, slug: str) -> ContentProject | None:
        stmt = self._base_query().where(ContentProject.slug == slug)
        return (await self.session.execute(stmt)).scalar_one_or_none()


class ContentVersionRepository(BaseRepository[ContentVersion]):
    model = ContentVersion

    async def list_for_project(self, project_id: uuid.UUID) -> list[ContentVersion]:
        stmt = (select(ContentVersion)
                .where(ContentVersion.project_id == project_id)
                .order_by(ContentVersion.version_number.desc()))
        return list((await self.session.execute(stmt)).scalars())

    async def next_version_number(self, project_id: uuid.UUID) -> int:
        stmt = select(func.max(ContentVersion.version_number)).where(
            ContentVersion.project_id == project_id
        )
        current = (await self.session.execute(stmt)).scalar_one_or_none()
        return (current or 0) + 1
