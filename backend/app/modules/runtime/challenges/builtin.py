"""Built-in challenge types: quiz, ordering, text_input, matching.

Each validates its config with a Pydantic model, projects a public view with
answers stripped, and evaluates responses. Further types (hotspot,
flashcards, code_challenge, simulation, decision_tree) register through the
same interface in the content slice.
"""
from typing import Any

from pydantic import BaseModel, Field, ValidationError, model_validator

from app.core.errors import ValidationFailedError
from app.modules.runtime.challenges.registry import ChallengeResult, register


def _validated(model: type[BaseModel], config: dict[str, Any]) -> BaseModel:
    try:
        return model.model_validate(config)
    except ValidationError as exc:
        raise ValidationFailedError(
            "Invalid challenge config", details={"errors": exc.errors(include_url=False)}
        ) from exc


# ── quiz: single or multiple select ────────────────────────────
class _QuizOption(BaseModel):
    id: str
    text: str


class _QuizConfig(BaseModel):
    question: str
    options: list[_QuizOption] = Field(min_length=2)
    correct: list[str] = Field(min_length=1)
    explanation: str = ""

    @model_validator(mode="after")
    def _correct_ids_exist(self) -> "_QuizConfig":
        ids = {o.id for o in self.options}
        unknown = [c for c in self.correct if c not in ids]
        if unknown:
            raise ValueError(f"correct references unknown option ids: {unknown}")
        if len(ids) != len(self.options):
            raise ValueError("option ids must be unique")
        return self


class Quiz:
    type_name = "quiz"

    def validate_config(self, config: dict[str, Any]) -> None:
        _validated(_QuizConfig, config)

    def public_config(self, config: dict[str, Any]) -> dict[str, Any]:
        cfg = _validated(_QuizConfig, config)
        return {
            "question": cfg.question,
            "options": [o.model_dump() for o in cfg.options],
            "multi_select": len(cfg.correct) > 1,
        }

    def evaluate(self, config: dict[str, Any], response: dict[str, Any]) -> ChallengeResult:
        cfg = _validated(_QuizConfig, config)
        selected = set(response.get("selected", []))
        correct = selected == set(cfg.correct)
        return ChallengeResult(
            correct=correct,
            score=1.0 if correct else 0.0,
            feedback=cfg.explanation if correct else "",
        )


# ── ordering: arrange items in sequence ────────────────────────
class _OrderingItem(BaseModel):
    id: str
    text: str


class _OrderingConfig(BaseModel):
    prompt: str
    items: list[_OrderingItem] = Field(min_length=2)
    correct_order: list[str]

    @model_validator(mode="after")
    def _order_matches_items(self) -> "_OrderingConfig":
        if sorted(self.correct_order) != sorted(i.id for i in self.items):
            raise ValueError("correct_order must be a permutation of item ids")
        return self


class Ordering:
    type_name = "ordering"

    def validate_config(self, config: dict[str, Any]) -> None:
        _validated(_OrderingConfig, config)

    def public_config(self, config: dict[str, Any]) -> dict[str, Any]:
        cfg = _validated(_OrderingConfig, config)
        return {"prompt": cfg.prompt, "items": [i.model_dump() for i in cfg.items]}

    def evaluate(self, config: dict[str, Any], response: dict[str, Any]) -> ChallengeResult:
        cfg = _validated(_OrderingConfig, config)
        order = response.get("order", [])
        if len(order) != len(cfg.correct_order):
            return ChallengeResult(correct=False, score=0.0)
        # strict=True is safe: the length check above already guarantees a pair
        # for every element, and it turns any future drift into a loud error.
        in_place = sum(
            1 for got, want in zip(order, cfg.correct_order, strict=True) if got == want
        )
        score = in_place / len(cfg.correct_order)
        return ChallengeResult(correct=score == 1.0, score=score)


# ── text_input: free-text answer ───────────────────────────────
class _TextInputConfig(BaseModel):
    prompt: str
    answers: list[str] = Field(min_length=1)
    case_sensitive: bool = False


class TextInput:
    type_name = "text_input"

    def validate_config(self, config: dict[str, Any]) -> None:
        _validated(_TextInputConfig, config)

    def public_config(self, config: dict[str, Any]) -> dict[str, Any]:
        cfg = _validated(_TextInputConfig, config)
        return {"prompt": cfg.prompt}

    def evaluate(self, config: dict[str, Any], response: dict[str, Any]) -> ChallengeResult:
        cfg = _validated(_TextInputConfig, config)
        text = str(response.get("text", "")).strip()
        answers = cfg.answers if cfg.case_sensitive else [a.lower() for a in cfg.answers]
        candidate = text if cfg.case_sensitive else text.lower()
        correct = candidate in answers
        return ChallengeResult(correct=correct, score=1.0 if correct else 0.0)


# ── matching: sort items into 2+ categories ────────────────────
class _MatchingCategory(BaseModel):
    id: str
    label: str


class _MatchingItem(BaseModel):
    id: str
    text: str
    category: str  # answer key — id of the correct _MatchingCategory


class _MatchingConfig(BaseModel):
    prompt: str
    categories: list[_MatchingCategory] = Field(min_length=2)
    items: list[_MatchingItem] = Field(min_length=2)

    @model_validator(mode="after")
    def _items_reference_known_categories(self) -> "_MatchingConfig":
        cat_ids = {c.id for c in self.categories}
        if len(cat_ids) != len(self.categories):
            raise ValueError("category ids must be unique")
        item_ids = {i.id for i in self.items}
        if len(item_ids) != len(self.items):
            raise ValueError("item ids must be unique")
        unknown = [i.id for i in self.items if i.category not in cat_ids]
        if unknown:
            raise ValueError(f"items reference unknown category ids: {unknown}")
        return self


class Matching:
    type_name = "matching"

    def validate_config(self, config: dict[str, Any]) -> None:
        _validated(_MatchingConfig, config)

    def public_config(self, config: dict[str, Any]) -> dict[str, Any]:
        cfg = _validated(_MatchingConfig, config)
        return {
            "prompt": cfg.prompt,
            "categories": [c.model_dump() for c in cfg.categories],
            # items shipped WITHOUT `category` — that's the answer key
            "items": [{"id": i.id, "text": i.text} for i in cfg.items],
        }

    def evaluate(self, config: dict[str, Any], response: dict[str, Any]) -> ChallengeResult:
        cfg = _validated(_MatchingConfig, config)
        answer_key = {i.id: i.category for i in cfg.items}
        placements: dict[str, str] = response.get("placements", {})
        correct_count = sum(
            1 for item_id, cat_id in answer_key.items()
            if placements.get(item_id) == cat_id
        )
        score = correct_count / len(answer_key)
        return ChallengeResult(correct=score == 1.0, score=score)


def register_builtin_types() -> None:
    register(Quiz())
    register(Ordering())
    register(TextInput())
    register(Matching())
