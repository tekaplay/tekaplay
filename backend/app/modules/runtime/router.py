import uuid

from fastapi import APIRouter, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Bus, CurrentUser, DbSession, require_permission
from app.events.bus import EventBus
from app.modules.runtime.schemas import (
    AnswerOut,
    AnswerRequest,
    ChooseRequest,
    DefinitionOut,
    PublishDefinitionRequest,
    SaveOut,
    SaveRequest,
    SessionView,
    StartSessionRequest,
)
from app.modules.runtime.service import RuntimeService, build_runtime_service

router = APIRouter(prefix="/runtime", tags=["runtime"])


def _service(session: AsyncSession, bus: EventBus) -> RuntimeService:
    return build_runtime_service(session, bus)


# ── Definitions (library) ──────────────────────────────────────
@router.get("/definitions", response_model=list[DefinitionOut])
async def list_definitions(
    _: CurrentUser, session: DbSession, bus: Bus,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[DefinitionOut]:
    records = await _service(session, bus).list_definitions(limit=limit, offset=offset)
    return [DefinitionOut.model_validate(r) for r in records]


@router.post("/definitions", response_model=DefinitionOut, status_code=201,
             dependencies=[require_permission("content.publish")])
async def publish_definition(
    body: PublishDefinitionRequest, current_user: CurrentUser,
    session: DbSession, bus: Bus,
) -> DefinitionOut:
    record = await _service(session, bus).publish_definition(
        slug=body.slug, raw=body.definition, published_by=current_user.id
    )
    return DefinitionOut.model_validate(record)


@router.get("/definitions/{definition_id}", response_model=DefinitionOut)
async def get_definition(
    definition_id: uuid.UUID, _: CurrentUser, session: DbSession, bus: Bus
) -> DefinitionOut:
    record = await _service(session, bus).get_definition(definition_id)
    return DefinitionOut.model_validate(record)


# ── Sessions ───────────────────────────────────────────────────
@router.post("/sessions", response_model=SessionView, status_code=201)
async def start_session(
    body: StartSessionRequest, current_user: CurrentUser,
    session: DbSession, bus: Bus,
) -> SessionView:
    return await _service(session, bus).start_session(
        user_id=current_user.id, definition_id=body.definition_id, replay=body.replay
    )


@router.get("/sessions/{session_id}", response_model=SessionView)
async def get_session_view(
    session_id: uuid.UUID, current_user: CurrentUser, session: DbSession, bus: Bus
) -> SessionView:
    return await _service(session, bus).get_view(
        user_id=current_user.id, session_id=session_id
    )


@router.post("/sessions/{session_id}/advance", response_model=SessionView)
async def advance(
    session_id: uuid.UUID, current_user: CurrentUser, session: DbSession, bus: Bus
) -> SessionView:
    return await _service(session, bus).advance(
        user_id=current_user.id, session_id=session_id
    )


@router.post("/sessions/{session_id}/choose", response_model=SessionView)
async def choose(
    session_id: uuid.UUID, body: ChooseRequest, current_user: CurrentUser,
    session: DbSession, bus: Bus,
) -> SessionView:
    return await _service(session, bus).choose(
        user_id=current_user.id, session_id=session_id,
        element_id=body.element_id, option_id=body.option_id,
    )


@router.post("/sessions/{session_id}/answer", response_model=AnswerOut)
async def answer(
    session_id: uuid.UUID, body: AnswerRequest, current_user: CurrentUser,
    session: DbSession, bus: Bus,
) -> AnswerOut:
    return await _service(session, bus).answer(
        user_id=current_user.id, session_id=session_id,
        element_id=body.element_id, response=body.response,
    )


# ── Save points ────────────────────────────────────────────────
@router.post("/sessions/{session_id}/saves", response_model=SaveOut, status_code=201)
async def create_save(
    session_id: uuid.UUID, body: SaveRequest, current_user: CurrentUser,
    session: DbSession, bus: Bus,
) -> SaveOut:
    save = await _service(session, bus).create_save(
        user_id=current_user.id, session_id=session_id, label=body.label
    )
    return SaveOut.model_validate(save)


@router.get("/sessions/{session_id}/saves", response_model=list[SaveOut])
async def list_saves(
    session_id: uuid.UUID, current_user: CurrentUser, session: DbSession, bus: Bus
) -> list[SaveOut]:
    saves = await _service(session, bus).list_saves(
        user_id=current_user.id, session_id=session_id
    )
    return [SaveOut.model_validate(s) for s in saves]


@router.post("/sessions/{session_id}/saves/{save_id}/restore",
             response_model=SessionView)
async def restore_save(
    session_id: uuid.UUID, save_id: uuid.UUID, current_user: CurrentUser,
    session: DbSession, bus: Bus,
) -> SessionView:
    return await _service(session, bus).restore_save(
        user_id=current_user.id, session_id=session_id, save_id=save_id
    )
