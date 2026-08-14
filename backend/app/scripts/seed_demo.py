"""Publish the example mission: python -m app.scripts.seed_demo"""
import asyncio
import json
from pathlib import Path

from app.db.session import SessionFactory
from app.events.bus import InProcessEventBus
from app.modules.users import models as _user_models  # noqa: F401
from app.modules.runtime.repository import (
    GameDefinitionRepository,
    GameSessionRepository,
    SavePointRepository,
)
from app.modules.runtime.service import RuntimeService

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "aws_cp_mission_1.json"


async def main() -> None:
    raw = json.loads(EXAMPLE.read_text())
    async with SessionFactory() as session:
        service = RuntimeService(
            definitions=GameDefinitionRepository(session),
            sessions=GameSessionRepository(session),
            saves=SavePointRepository(session),
            event_bus=InProcessEventBus(),
        )
        record = await service.publish_definition(slug="aws-cp-mission-1", raw=raw)
        await session.commit()
        print(f"published: {record.slug} ({record.id})")


if __name__ == "__main__":
    asyncio.run(main())
