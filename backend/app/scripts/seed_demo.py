"""Publish the example missions: python -m app.scripts.seed_demo"""
import asyncio
import json
from pathlib import Path

from app.db.session import SessionFactory
from app.events.bus import InProcessEventBus
from app.modules.runtime.repository import (
    GameDefinitionRepository,
    GameSessionRepository,
    SavePointRepository,
)
from app.modules.runtime.service import RuntimeService
from app.modules.users import models as _user_models  # noqa: F401

EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "examples"

MISSIONS = [
    ("calculus-mission-1", "calculus_mission_1.json"),
    ("advanced-functions-mission-1", "advanced_functions_mission_1.json"),
    ("biology-mission-1", "biology_mission_1.json"),
    ("physics-mission-1", "physics_mission_1.json"),
    ("data-management-mission-1", "data_management_mission_1.json"),
    ("intro-cs-mission-1", "intro_cs_mission_1.json"),
]


async def main() -> None:
    async with SessionFactory() as session:
        service = RuntimeService(
            definitions=GameDefinitionRepository(session),
            sessions=GameSessionRepository(session),
            saves=SavePointRepository(session),
            event_bus=InProcessEventBus(),
        )
        for slug, filename in MISSIONS:
            raw = json.loads((EXAMPLES_DIR / filename).read_text())
            record = await service.publish_definition(slug=slug, raw=raw)
            print(f"published: {record.slug} ({record.id})")
        await session.commit()


if __name__ == "__main__":
    asyncio.run(main())
