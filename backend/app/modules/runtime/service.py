"""Runtime orchestration: ownership, persistence, concurrency, event emission.

The interpreter (engine.py) is pure; this service loads the session, runs one
engine operation, persists the new state document under optimistic locking,
and publishes the pending events as DomainEvents enriched with user/session/
definition identifiers.

Known tradeoff, documented: events publish in-process before the request's
transaction commits. Acceptable for the in-process bus (subscribers are
idempotent); the durable-broker slice replaces this with a transactional
outbox.
"""
import copy
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm.exc import StaleDataError

from app.core.errors import ConflictError, NotFoundError
from app.events.bus import DomainEvent, EventBus
from app.modules.runtime import engine
from app.modules.runtime import events as ev
from app.modules.runtime.definition import GameDefinition, parse_definition
from app.modules.runtime.effects import PendingEvent
from app.modules.runtime.models import GameDefinitionRecord, GameSession, SavePoint
from app.modules.runtime.repository import (
    GameDefinitionRepository,
    GameSessionRepository,
    SavePointRepository,
)
from app.modules.runtime.schemas import HUD, AnswerOut, SessionView
from app.services.base import BaseService


class RuntimeService(BaseService):
    def __init__(
        self,
        definitions: GameDefinitionRepository,
        sessions: GameSessionRepository,
        saves: SavePointRepository,
        event_bus: EventBus,
    ) -> None:
        super().__init__(event_bus)
        self._definitions = definitions
        self._sessions = sessions
        self._saves = saves

    # ── Definitions ────────────────────────────────────────────
    async def publish_definition(
        self, *, slug: str, raw: dict[str, Any],
        published_by: uuid.UUID | None = None,
    ) -> GameDefinitionRecord:
        """Direct publish of a NEW slug (seed scripts, imports). Slugs with a
        live definition must go through the content lifecycle instead."""
        existing = await self._definitions.get_live_by_slug(slug)
        if existing is not None:
            raise ConflictError("A live definition with this slug already exists",
                                details={"slug": slug})
        return await self._create_live(slug=slug, raw=raw, published_by=published_by)

    async def upsert_live_definition(
        self, *, slug: str, raw: dict[str, Any],
        published_by: uuid.UUID | None = None,
    ) -> GameDefinitionRecord:
        """Content-lifecycle entry point: retire the current live row (if any)
        and create a new immutable row. In-flight sessions keep their old
        definition_id and are untouched."""
        current = await self._definitions.get_live_by_slug(slug)
        if current is not None:
            current.live = False
        return await self._create_live(slug=slug, raw=raw, published_by=published_by)

    async def _create_live(self, *, slug: str, raw: dict[str, Any],
                           published_by: uuid.UUID | None) -> GameDefinitionRecord:
        defn = parse_definition(raw)  # the publish-time gate
        record = GameDefinitionRecord(
            slug=slug,
            live=True,
            title=defn.title,
            certification=defn.certification,
            schema_version=defn.schema_version,
            definition=raw,
            published_by=published_by,
        )
        self._definitions.add(record)
        await self._definitions.flush()
        await self.emit(DomainEvent(name="content.published", user_id=published_by,
                                    payload={"definition_id": str(record.id),
                                             "slug": slug}))
        return record

    async def list_definitions(self, *, limit: int, offset: int) -> list[GameDefinitionRecord]:
        return await self._definitions.list_live(limit=limit, offset=offset)

    async def live_definitions_by_slug(self) -> dict[str, GameDefinitionRecord]:
        return await self._definitions.live_slug_map()

    async def get_definition(self, definition_id: uuid.UUID) -> GameDefinitionRecord:
        return await self._definitions.get(definition_id)

    # ── Sessions ───────────────────────────────────────────────
    async def start_session(self, *, user_id: uuid.UUID, definition_id: uuid.UUID,
                            replay: bool = False) -> SessionView:
        record = await self._definitions.get(definition_id)
        if not replay:
            existing = await self._sessions.get_active(user_id, definition_id)
            if existing is not None:  # resume anywhere
                await self._publish(existing, [(ev.SESSION_RESUMED, {})], slug=record.slug)
                return self._view(record, existing)
        defn = self._parse(record)
        state, pending = engine.new_state(defn)
        session = GameSession(user_id=user_id, definition_id=definition_id,
                              status=engine.STATUS_ACTIVE, state=state)
        self._sessions.add(session)
        await self._flush()
        await self._publish(session, [(ev.MISSION_STARTED, {})] + pending, slug=record.slug)
        return self._view(record, session)

    async def get_view(self, *, user_id: uuid.UUID, session_id: uuid.UUID) -> SessionView:
        session = await self._owned_session(user_id, session_id)
        record = await self._definitions.get(session.definition_id)
        return self._view(record, session)

    async def advance(self, *, user_id: uuid.UUID, session_id: uuid.UUID) -> SessionView:
        session = await self._owned_session(user_id, session_id)
        record = await self._definitions.get(session.definition_id)
        defn = self._parse(record)
        state = copy.deepcopy(session.state)
        pending = engine.advance(defn, state)
        await self._persist(session, state)
        await self._publish(session, pending, slug=record.slug)
        return self._view(record, session)

    async def choose(self, *, user_id: uuid.UUID, session_id: uuid.UUID,
                     element_id: str, option_id: str) -> SessionView:
        session = await self._owned_session(user_id, session_id)
        record = await self._definitions.get(session.definition_id)
        defn = self._parse(record)
        state = copy.deepcopy(session.state)
        pending = engine.choose(defn, state, element_id, option_id)
        await self._persist(session, state)
        await self._publish(session, pending, slug=record.slug)
        return self._view(record, session)

    async def answer(self, *, user_id: uuid.UUID, session_id: uuid.UUID,
                     element_id: str, response: dict[str, Any]) -> AnswerOut:
        session = await self._owned_session(user_id, session_id)
        record = await self._definitions.get(session.definition_id)
        defn = self._parse(record)
        state = copy.deepcopy(session.state)
        result, pending = engine.answer(defn, state, element_id, response)
        await self._persist(session, state)
        await self._publish(session, pending, slug=record.slug)
        return AnswerOut(correct=result.correct, score=result.score,
                         feedback=result.feedback, view=self._view(record, session))

    # ── Save points ────────────────────────────────────────────
    async def create_save(self, *, user_id: uuid.UUID, session_id: uuid.UUID,
                          label: str) -> SavePoint:
        session = await self._owned_session(user_id, session_id)
        save = SavePoint(session_id=session.id, label=label,
                         state=copy.deepcopy(session.state))
        self._saves.add(save)
        await self._flush()
        await self._publish(session, [(ev.SAVE_CREATED, {"save_id": str(save.id),
                                                         "label": label})])
        return save

    async def list_saves(self, *, user_id: uuid.UUID,
                         session_id: uuid.UUID) -> list[SavePoint]:
        session = await self._owned_session(user_id, session_id)
        return await self._saves.list_for_session(session.id)

    async def restore_save(self, *, user_id: uuid.UUID, session_id: uuid.UUID,
                           save_id: uuid.UUID) -> SessionView:
        session = await self._owned_session(user_id, session_id)
        save = await self._saves.get(save_id)
        if save.session_id != session.id:
            raise NotFoundError("Save point not found", details={"id": str(save_id)})
        record = await self._definitions.get(session.definition_id)
        restored = copy.deepcopy(save.state)
        session.status = restored.get("status", engine.STATUS_ACTIVE)
        session.ending_id = restored.get("ending_id")
        session.completed_at = (
            None if session.status == engine.STATUS_ACTIVE else session.completed_at
        )
        await self._persist(session, restored)
        await self._publish(session, [(ev.SESSION_RESUMED, {"from_save": str(save_id)})],
                            slug=record.slug)
        return self._view(record, session)

    # ── Internals ──────────────────────────────────────────────
    async def _owned_session(self, user_id: uuid.UUID,
                             session_id: uuid.UUID) -> GameSession:
        session = await self._sessions.get(session_id)
        if session.user_id != user_id:  # 404, not 403: don't leak existence
            raise NotFoundError("Session not found", details={"id": str(session_id)})
        return session

    def _parse(self, record: GameDefinitionRecord) -> GameDefinition:
        return parse_definition(record.definition)

    async def _persist(self, session: GameSession, state: dict[str, Any]) -> None:
        session.state = state  # reassignment (not mutation) marks the column dirty
        if state["status"] == engine.STATUS_COMPLETED and session.status != state["status"]:
            session.completed_at = datetime.now(UTC)
            session.ending_id = state.get("ending_id")
        session.status = state["status"]
        await self._flush()

    async def _flush(self) -> None:
        try:
            await self._sessions.flush()
        except StaleDataError as exc:
            raise ConflictError(
                "This session was updated by another request — reload and retry"
            ) from exc

    async def _publish(self, session: GameSession, pending: list[PendingEvent],
                       slug: str | None = None) -> None:
        base = {"session_id": str(session.id),
                "definition_id": str(session.definition_id)}
        if slug is not None:
            base["definition_slug"] = slug
        for name, payload in pending:
            await self.emit(DomainEvent(name=name, user_id=session.user_id,
                                        payload={**base, **payload}))

    def _view(self, record: GameDefinitionRecord, session: GameSession) -> SessionView:
        defn = self._parse(record)
        view = engine.compute_view(defn, session.state)
        scene_id = session.state.get("scene_id")
        scene = defn.scenes.get(scene_id) if scene_id else None
        state = session.state
        return SessionView(
            session_id=session.id,
            definition_id=session.definition_id,
            status=session.status,
            scene_id=scene_id,
            scene_title=scene.title if scene else None,
            passives=view.passives,
            interactive=view.interactive,
            can_advance=view.can_advance,
            ending=view.ending,
            hud=HUD(
                variables=state.get("variables", {}),
                flags=state.get("flags", []),
                inventory=state.get("inventory", {}),
                xp_earned=state.get("xp_earned", 0),
                achievements=state.get("achievements", []),
            ),
        )


def build_runtime_service(session, event_bus: EventBus) -> RuntimeService:
    """Composition helper — the only sanctioned way other modules obtain the
    runtime's service interface (module boundary rule)."""
    return RuntimeService(
        definitions=GameDefinitionRepository(session),
        sessions=GameSessionRepository(session),
        saves=SavePointRepository(session),
        event_bus=event_bus,
    )
