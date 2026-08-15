"""Game definition schema v1 — the single most important contract in the platform.

Games are data: a definition is a validated JSON document describing scenes as
nodes in a directed graph, with elements (dialogue, media, choices, challenges),
conditions gating branches, and effects producing rewards. The runtime
interprets this; nothing certification-specific ever becomes code.

Evolution policy: additive only. New element types, effect ops, and fields may
be added under schema_version 1; anything breaking increments schema_version
and ships alongside a migrator. Published definitions are immutable.
"""
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, model_validator

from app.core.errors import ValidationFailedError
from app.modules.runtime import challenges
from app.modules.runtime.conditions import Condition
from app.modules.runtime.effects import Effect


class NPC(BaseModel):
    name: str
    role: str = ""
    description: str = ""


class Item(BaseModel):
    name: str
    description: str = ""


class DialogueElement(BaseModel):
    type: Literal["dialogue"]
    npc: str | None = None  # None = narrator
    text: str
    condition: Condition | None = None  # gated lines: NPCs react to memory


class MediaElement(BaseModel):
    type: Literal["media"]
    kind: Literal["image", "audio", "video"]
    asset_id: str  # resolved to a URL by the asset service, never a raw URL
    caption: str = ""
    condition: Condition | None = None


class ChoiceOption(BaseModel):
    id: str
    text: str
    condition: Condition | None = None  # hidden when false
    effects: list[Effect] = Field(default_factory=list)
    goto: str | None = None  # None = continue in current scene


class ChoiceElement(BaseModel):
    type: Literal["choice"]
    id: str
    prompt: str
    options: list[ChoiceOption] = Field(min_length=1)


class Outcome(BaseModel):
    effects: list[Effect] = Field(default_factory=list)
    goto: str | None = None


class ChallengeElement(BaseModel):
    type: Literal["challenge"]
    id: str
    challenge_type: str
    config: dict
    max_attempts: int = Field(default=3, ge=1)
    on_correct: Outcome = Field(default_factory=Outcome)
    on_incorrect: Outcome = Field(default_factory=Outcome)  # applied per wrong attempt
    on_exhausted: Outcome | None = None  # followed when attempts run out


Element = Annotated[
    Union[DialogueElement, MediaElement, ChoiceElement, ChallengeElement],
    Field(discriminator="type"),
]


class Ending(BaseModel):
    id: str
    title: str
    description: str = ""


class Scene(BaseModel):
    title: str
    elements: list[Element] = Field(default_factory=list)
    on_enter: list[Effect] = Field(default_factory=list)
    next: str | None = None  # default transition when elements are exhausted
    ending: Ending | None = None  # terminal scene


class GameDefinition(BaseModel):
    schema_version: Literal[1]
    title: str
    description: str = ""
    certification: str = ""  # taxonomy label only — never drives logic
    start_scene: str
    variables: dict[str, int | float | str | bool] = Field(default_factory=dict)
    npcs: dict[str, NPC] = Field(default_factory=dict)
    items: dict[str, Item] = Field(default_factory=dict)
    scenes: dict[str, Scene]

    @model_validator(mode="after")
    def _validate_graph(self) -> "GameDefinition":
        problems: list[str] = []
        if self.start_scene not in self.scenes:
            problems.append(f"start_scene '{self.start_scene}' does not exist")

        def check_target(target: str | None, where: str) -> None:
            if target is not None and target not in self.scenes:
                problems.append(f"{where} → unknown scene '{target}'")

        for scene_id, scene in self.scenes.items():
            check_target(scene.next, f"scene '{scene_id}'.next")
            if scene.next is None and scene.ending is None:
                # legal only if every path leaves via a goto; require at least one
                has_exit = any(
                    (isinstance(el, ChoiceElement) and any(o.goto for o in el.options))
                    or (isinstance(el, ChallengeElement)
                        and (el.on_correct.goto or (el.on_exhausted and el.on_exhausted.goto)))
                    for el in scene.elements
                )
                if not has_exit:
                    problems.append(
                        f"scene '{scene_id}' has no next, ending, or branching exit"
                    )
            element_ids: set[str] = set()
            for el in scene.elements:
                if isinstance(el, ChoiceElement):
                    for option in el.options:
                        check_target(option.goto,
                                     f"scene '{scene_id}' choice '{el.id}' option '{option.id}'")
                    if len({o.id for o in el.options}) != len(el.options):
                        problems.append(f"scene '{scene_id}' choice '{el.id}' has duplicate option ids")
                if isinstance(el, ChallengeElement):
                    for outcome_name, outcome in (
                        ("on_correct", el.on_correct),
                        ("on_incorrect", el.on_incorrect),
                        ("on_exhausted", el.on_exhausted),
                    ):
                        if outcome is not None:
                            check_target(outcome.goto,
                                         f"scene '{scene_id}' challenge '{el.id}'.{outcome_name}")
                    try:
                        challenges.get(el.challenge_type).validate_config(el.config)
                    except ValidationFailedError as exc:
                        problems.append(
                            f"scene '{scene_id}' challenge '{el.id}': {exc.message} "
                            f"{exc.details}"
                        )
                if isinstance(el, (ChoiceElement, ChallengeElement)):
                    if el.id in element_ids:
                        problems.append(f"scene '{scene_id}' has duplicate element id '{el.id}'")
                    element_ids.add(el.id)

        if problems:
            raise ValueError("; ".join(problems))
        return self


def parse_definition(raw: dict) -> GameDefinition:
    """Publish-time gate: a definition that parses here is safe to interpret."""
    from pydantic import ValidationError

    try:
        return GameDefinition.model_validate(raw)
    except ValidationError as exc:
        # include_context=False drops pydantic's `ctx`, which carries the
        # original exception object for validators that raise ValueError and
        # is not JSON-serializable; include_input=False keeps the offending
        # (potentially huge) raw definition out of the error envelope. The
        # human-readable reason survives in each error's `msg`.
        raise ValidationFailedError(
            "Game definition failed validation",
            details={"errors": exc.errors(include_url=False, include_context=False,
                                          include_input=False)},
        ) from exc
