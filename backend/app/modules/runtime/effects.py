"""Effect grammar: the only way game content mutates player state.

Every effect application returns the domain events it implies, so rewards
flow to XP/achievements/analytics purely through the bus — the runtime never
calls those modules.
"""
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


class SetVar(BaseModel):
    op: Literal["set_var"]
    key: str
    value: int | float | str | bool


class IncVar(BaseModel):
    op: Literal["inc_var"]
    key: str
    amount: float = 1


class SetFlag(BaseModel):
    op: Literal["set_flag"]
    key: str
    value: bool = True


class GrantItem(BaseModel):
    op: Literal["grant_item"]
    item: str
    qty: int = Field(default=1, ge=1)


class RemoveItem(BaseModel):
    op: Literal["remove_item"]
    item: str
    qty: int = Field(default=1, ge=1)


class AwardXp(BaseModel):
    op: Literal["award_xp"]
    amount: int = Field(ge=0)
    reason: str = ""


class UnlockAchievement(BaseModel):
    op: Literal["unlock_achievement"]
    code: str


Effect = Annotated[
    SetVar | IncVar | SetFlag | GrantItem | RemoveItem | AwardXp | UnlockAchievement,
    Field(discriminator="op"),
]

PendingEvent = tuple[str, dict[str, Any]]  # (event_name, payload)


def apply_effects(state: dict[str, Any], effects: list[Effect]) -> list[PendingEvent]:
    events: list[PendingEvent] = []
    variables = state.setdefault("variables", {})
    flags: list[str] = state.setdefault("flags", [])
    inventory: dict[str, int] = state.setdefault("inventory", {})

    for effect in effects:
        if isinstance(effect, SetVar):
            variables[effect.key] = effect.value
        elif isinstance(effect, IncVar):
            variables[effect.key] = variables.get(effect.key, 0) + effect.amount
        elif isinstance(effect, SetFlag):
            if effect.value and effect.key not in flags:
                flags.append(effect.key)
            elif not effect.value and effect.key in flags:
                flags.remove(effect.key)
        elif isinstance(effect, GrantItem):
            inventory[effect.item] = inventory.get(effect.item, 0) + effect.qty
            events.append(("inventory.changed",
                           {"item": effect.item, "delta": effect.qty,
                            "qty": inventory[effect.item]}))
        elif isinstance(effect, RemoveItem):
            new_qty = max(0, inventory.get(effect.item, 0) - effect.qty)
            delta = new_qty - inventory.get(effect.item, 0)
            if new_qty == 0:
                inventory.pop(effect.item, None)
            else:
                inventory[effect.item] = new_qty
            events.append(("inventory.changed",
                           {"item": effect.item, "delta": delta, "qty": new_qty}))
        elif isinstance(effect, AwardXp):
            state["xp_earned"] = state.get("xp_earned", 0) + effect.amount
            events.append(("xp.awarded", {"amount": effect.amount, "reason": effect.reason}))
        elif isinstance(effect, UnlockAchievement):
            unlocked: list[str] = state.setdefault("achievements", [])
            if effect.code not in unlocked:
                unlocked.append(effect.code)
                events.append(("achievement.unlocked", {"code": effect.code}))
    return events
