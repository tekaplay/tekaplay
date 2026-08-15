"""Single place where modules attach to the application.

Adding a module = one line in each function here; no module ever edits
another module to integrate.
"""
from fastapi import APIRouter

from app.events.bus import EventBus


def mount_routers(api_router: APIRouter) -> None:
    from app.modules.achievements.router import router as achievements_router
    from app.modules.ai.router import router as ai_router
    from app.modules.commerce.router import router as commerce_router
    from app.modules.auth.router import router as auth_router
    from app.modules.content.router import router as content_router
    from app.modules.inventory.router import router as inventory_router
    from app.modules.organizations.router import router as organizations_router
    from app.modules.progress.router import router as progress_router
    from app.modules.runtime.router import router as runtime_router
    from app.modules.users.router import router as users_router
    from app.modules.xp.router import router as xp_router

    api_router.include_router(auth_router)
    api_router.include_router(users_router)
    api_router.include_router(runtime_router)
    api_router.include_router(content_router)
    api_router.include_router(progress_router)
    api_router.include_router(xp_router)
    api_router.include_router(achievements_router)
    api_router.include_router(inventory_router)
    api_router.include_router(ai_router)
    api_router.include_router(commerce_router)
    api_router.include_router(organizations_router)


def wire_event_subscribers(bus: EventBus) -> None:
    from app.modules.achievements import events as achievements_events
    from app.modules.auth import events as auth_events
    from app.modules.inventory import events as inventory_events
    from app.modules.notifications import events as notifications_events
    from app.modules.progress import events as progress_events
    from app.modules.xp import events as xp_events

    auth_events.register(bus)
    xp_events.register(bus)
    achievements_events.register(bus)
    progress_events.register(bus)
    inventory_events.register(bus)
    notifications_events.register(bus)
