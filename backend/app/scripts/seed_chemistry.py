"""Seed the Chemistry (Nelson 11/12) curriculum: python -m app.scripts.seed_chemistry

Builds the real Certification -> Campaign -> Course -> Mission catalog and
runs every authored mission through the actual draft -> submit -> approve ->
publish ContentVersion pipeline, instead of the flat runtime-only shortcut
seed_demo.py uses. Missions without content yet are still registered as
catalog rows (project_id left null) so the full table of contents is visible
before every mission is authored.

Idempotent: safe to re-run. Existing catalog rows are reused by slug; a
mission whose ContentProject already exists is left alone rather than
re-drafted.
"""
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from app.db.session import SessionFactory
from app.events.bus import InProcessEventBus
from app.modules.content.models import Campaign, Certification, ContentProject, Course, Mission
from app.modules.content.repository import (
    CampaignRepository,
    CertificationRepository,
    ContentProjectRepository,
    ContentVersionRepository,
    CourseRepository,
    MissionRepository,
)
from app.modules.content.service import ContentService
from app.modules.runtime.service import build_runtime_service
from app.modules.users import models as _user_models  # noqa: F401
from app.modules.users.audit import AuditService

EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "examples" / "chemistry"

# Chem 11 moved from 5 unit-level missions to 12 chapter-level missions.
# These slugs described unit-spanning content that no longer exists at that
# grain, so they're retired (soft-deleted) rather than renamed/reused.
LEGACY_CHEM11_MISSION_SLUGS = [
    "chem11-m1-matter-bonding",
    "chem11-m2-chemical-reactions",
    "chem11-m3-quantities-stoichiometry",
    "chem11-m4-solutions-solubility",
    "chem11-m5-gases",
]

CERTIFICATION = {
    "slug": "high-school-chemistry",
    "title": "High School Chemistry",
    "description": "Chemistry 11 and 12, aligned to Nelson Chemistry 11 & 12.",
    "category": "high-school",
}

CAMPAIGN = {
    "slug": "chemistry-nelson",
    "title": "Chemistry (Nelson 11 and 12)",
}

# (course dict, [mission dicts]) — mission "file" is None for not-yet-authored
# chapters, so the full table of contents registers before every mission has
# playable content.
COURSES = [
    (
        {"slug": "chem-11", "title": "Chemistry 11",
         "unit_ref": "Nelson Chemistry 11", "sort_order": 0, "is_active": True},
        [
            {"slug": "chem11-c01-atomic-structure",
             "title": "Atomic Structure and the Periodic Table",
             "unit_ref": "Nelson Chemistry 11, Unit 1 Ch. 1: Atomic Structure and the Periodic Table",
             "file": "chem11-c01-atomic-structure.json"},
            {"slug": "chem11-c02-compounds-bonding",
             "title": "Chemical Compounds and Bonding",
             "unit_ref": "Nelson Chemistry 11, Unit 1 Ch. 2: Chemical Compounds and Bonding",
             "file": "chem11-c02-compounds-bonding.json"},
            {"slug": "chem11-c03-molecular-imf",
             "title": "Molecular Compounds and Intermolecular Forces",
             "unit_ref": "Nelson Chemistry 11, Unit 1 Ch. 3: Molecular Compounds and Intermolecular Forces",
             "file": "chem11-c03-molecular-imf.json"},
            {"slug": "chem11-c04-effects-of-reactions",
             "title": "The Effects of Chemical Reactions",
             "unit_ref": "Nelson Chemistry 11, Unit 2 Ch. 4: The Effects of Chemical Reactions",
             "file": "chem11-c04-effects-of-reactions.json"},
            {"slug": "chem11-c05-chemical-processes",
             "title": "Chemical Processes",
             "unit_ref": "Nelson Chemistry 11, Unit 2 Ch. 5: Chemical Processes",
             "file": "chem11-c05-chemical-processes.json"},
            {"slug": "chem11-c06-quantities-formulas",
             "title": "Quantities in Chemical Formulas",
             "unit_ref": "Nelson Chemistry 11, Unit 3 Ch. 6: Quantities in Chemical Formulas",
             "file": "chem11-c06-quantities-formulas.json"},
            {"slug": "chem11-c07-stoichiometry",
             "title": "Stoichiometry in Chemical Reactions",
             "unit_ref": "Nelson Chemistry 11, Unit 3 Ch. 7: Stoichiometry in Chemical Reactions",
             "file": "chem11-c07-stoichiometry.json"},
            {"slug": "chem11-c08-water-solutions",
             "title": "Water and Solutions",
             "unit_ref": "Nelson Chemistry 11, Unit 4 Ch. 8: Water and Solutions",
             "file": "chem11-c08-water-solutions.json"},
            {"slug": "chem11-c09-solutions-reactions",
             "title": "Solutions and Their Reactions",
             "unit_ref": "Nelson Chemistry 11, Unit 4 Ch. 9: Solutions and Their Reactions",
             "file": "chem11-c09-solutions-reactions.json"},
            {"slug": "chem11-c10-acids-bases",
             "title": "Acids and Bases",
             "unit_ref": "Nelson Chemistry 11, Unit 4 Ch. 10: Acids and Bases",
             "file": "chem11-c10-acids-bases.json"},
            {"slug": "chem11-c11-gas-state-laws",
             "title": "The Gas State and Gas Laws",
             "unit_ref": "Nelson Chemistry 11, Unit 5 Ch. 11: The Gas State and Gas Laws",
             "file": "chem11-c11-gas-state-laws.json"},
            {"slug": "chem11-c12-gas-mixtures",
             "title": "Gas Laws, Gas Mixtures, and Gas Reactions",
             "unit_ref": "Nelson Chemistry 11, Unit 5 Ch. 12: Gas Laws, Gas Mixtures, and Gas Reactions",
             "file": "chem11-c12-gas-mixtures.json"},
        ],
    ),
    (
        {"slug": "chem-12", "title": "Chemistry 12",
         "unit_ref": "Nelson Chemistry 12", "sort_order": 1, "is_active": True},
        [
            {"slug": "chem12-m1-kinetics", "title": "Reaction Kinetics",
             "unit_ref": "Nelson Chemistry 12", "file": None},
            {"slug": "chem12-m2-equilibrium", "title": "Chemical Equilibrium",
             "unit_ref": "Nelson Chemistry 12", "file": None},
            {"slug": "chem12-m3-acids-bases", "title": "Acid-Base Equilibrium",
             "unit_ref": "Nelson Chemistry 12", "file": None},
            {"slug": "chem12-m4-solubility-equilibrium", "title": "Solubility Equilibrium",
             "unit_ref": "Nelson Chemistry 12", "file": None},
            {"slug": "chem12-m5-electrochemistry", "title": "Electrochemistry",
             "unit_ref": "Nelson Chemistry 12", "file": None},
        ],
    ),
]


async def _get_or_create_certification(service: ContentService,
                                       certs: CertificationRepository) -> Certification:
    existing = await certs.ordered()
    for c in existing:
        if c.slug == CERTIFICATION["slug"]:
            return c
    return await service.create_certification(CERTIFICATION)


async def _get_or_create_campaign(service: ContentService, campaigns: CampaignRepository,
                                  certification_id) -> Campaign:
    existing = await campaigns.ordered()
    for c in existing:
        if c.slug == CAMPAIGN["slug"] and c.certification_id == certification_id:
            return c
    return await service.create_campaign({**CAMPAIGN, "certification_id": certification_id})


async def _get_or_create_course(service: ContentService, courses: CourseRepository,
                                campaign_id, data: dict) -> Course:
    existing = await courses.ordered(include_inactive=True)
    for c in existing:
        if c.slug == data["slug"] and c.campaign_id == campaign_id:
            if c.is_active != data.get("is_active", True):
                c.is_active = data["is_active"]
                await courses.flush()
                print(f"  course visibility updated: {c.slug} -> "
                     f"is_active={c.is_active}")
            return c
    return await service.create_course({**data, "campaign_id": campaign_id})


async def _publish_mission_content(service: ContentService, filename: str, slug: str) -> None:
    raw = json.loads((EXAMPLES_DIR / filename).read_text())
    project = await service.create_project(
        slug=slug, title=raw["title"], certification=raw.get("certification", ""),
        owner_id=None,
    )
    draft = await service.create_draft(
        project_id=project.id, definition=raw, notes="Seeded from curriculum content.",
        actor=None,
    )
    submitted = await service.submit(version_id=draft.id, actor=None)
    approved = await service.approve(version_id=submitted.id, note="Seed approval.", actor=None)
    await service.publish(version_id=approved.id, actor=None)
    print(f"  published mission content: {slug}")


async def _retire_legacy_chem11_missions(
    missions_repo: MissionRepository, projects_repo: ContentProjectRepository, course_id,
) -> None:
    """Soft-delete the 5 old unit-level Chem 11 missions/projects, now
    replaced by 12 chapter-level ones. Idempotent: a no-op once retired,
    since soft-deleted rows are invisible to repository lookups. The
    underlying published game_definitions rows are left as immutable
    orphaned history, per the platform's append-only publishing model."""
    missions_by_slug = {m.slug: m for m in await missions_repo.ordered()
                        if m.course_id == course_id}
    for slug in LEGACY_CHEM11_MISSION_SLUGS:
        mission = missions_by_slug.get(slug)
        if mission is not None:
            mission.deleted_at = datetime.now(UTC)
            print(f"  retired legacy mission: {slug}")
        project = await projects_repo.get_by_slug(slug)
        if project is not None:
            project.deleted_at = datetime.now(UTC)
    await missions_repo.flush()


async def main() -> None:
    async with SessionFactory() as session:
        bus = InProcessEventBus()
        service = ContentService(
            projects=ContentProjectRepository(session),
            versions=ContentVersionRepository(session),
            certifications=CertificationRepository(session),
            campaigns=CampaignRepository(session),
            courses=CourseRepository(session),
            missions=MissionRepository(session),
            runtime=build_runtime_service(session, bus),
            audit=AuditService(session),
            event_bus=bus,
        )
        certs = CertificationRepository(session)
        campaigns_repo = CampaignRepository(session)
        courses_repo = CourseRepository(session)
        missions_repo = MissionRepository(session)
        projects_repo = ContentProjectRepository(session)

        certification = await _get_or_create_certification(service, certs)
        campaign = await _get_or_create_campaign(service, campaigns_repo, certification.id)
        print(f"certification: {certification.slug}, campaign: {campaign.slug}")

        for course_data, mission_defs in COURSES:
            course = await _get_or_create_course(service, courses_repo, campaign.id, course_data)
            print(f"course: {course.slug}")

            if course.slug == "chem-11":
                await _retire_legacy_chem11_missions(missions_repo, projects_repo, course.id)

            existing_missions = {m.slug: m for m in await missions_repo.ordered()
                                 if m.course_id == course.id}

            for i, mdef in enumerate(mission_defs):
                slug = mdef["slug"]
                existing_mission = existing_missions.get(slug)

                # Content became available for a chapter registered earlier
                # with no file yet — publish it now and attach it to the
                # already-existing mission row instead of skipping.
                if existing_mission is not None:
                    if existing_mission.project_id is None and mdef["file"] is not None:
                        existing_project = await projects_repo.get_by_slug(slug)
                        if existing_project is None:
                            await _publish_mission_content(service, mdef["file"], slug)
                            existing_project = await projects_repo.get_by_slug(slug)
                        existing_mission.project_id = existing_project.id
                        await missions_repo.flush()
                        print(f"  mission content attached: {slug}")
                    else:
                        print(f"  mission already registered: {slug}")
                    continue

                project_id = None
                if mdef["file"] is not None:
                    existing_project = await projects_repo.get_by_slug(slug)
                    if existing_project is not None:
                        project_id = existing_project.id
                    else:
                        await _publish_mission_content(service, mdef["file"], slug)
                        project = await projects_repo.get_by_slug(slug)
                        project_id = project.id

                await service.create_mission({
                    "course_id": course.id,
                    "slug": slug,
                    "title": mdef["title"],
                    "unit_ref": mdef["unit_ref"],
                    "sort_order": i,
                    "project_id": project_id,
                })
                print(f"  mission registered: {slug}"
                     f"{' (content pending)' if project_id is None else ''}")

        await session.commit()
        print("done.")


if __name__ == "__main__":
    asyncio.run(main())
