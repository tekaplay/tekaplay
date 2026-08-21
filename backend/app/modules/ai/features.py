"""AI feature registry — mirrors the challenge-type registry pattern.

A feature owns its input schema (validated at the API edge), whether it is
personalized (cache scope per user, enriched with progress context), and its
prompt builder. Adding a capability (adaptive difficulty, exam readiness,
mission generation, ...) is one class + one register() call; the service,
queueing, caching, persistence, and API never change.

Prompts must never contain secrets or other users' data; personalized
context is limited to the requesting user's own progress.
"""
from typing import Protocol

from pydantic import BaseModel, Field, ValidationError

from app.core.errors import ValidationFailedError


class AIFeature(Protocol):
    name: str
    description: str
    personalized: bool
    input_model: type[BaseModel]

    def build_prompt(self, data: BaseModel, context: dict) -> str: ...


_REGISTRY: dict[str, AIFeature] = {}


def register(feature: AIFeature) -> None:
    _REGISTRY[feature.name] = feature


def get(name: str) -> AIFeature:
    feature = _REGISTRY.get(name)
    if feature is None:
        raise ValidationFailedError("Unknown AI feature",
                                    details={"feature": name,
                                             "available": sorted(_REGISTRY)})
    return feature


def catalog() -> list[dict]:
    return [
        {"name": f.name, "description": f.description, "personalized": f.personalized}
        for f in sorted(_REGISTRY.values(), key=lambda f: f.name)
    ]


def validate_input(name: str, raw: dict) -> BaseModel:
    feature = get(name)
    try:
        return feature.input_model.model_validate(raw)
    except ValidationError as exc:
        raise ValidationFailedError(
            "Invalid input for AI feature",
            details={"feature": name, "errors": exc.errors(include_url=False)},
        ) from exc


# ── Built-in features ──────────────────────────────────────────
class _HintIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    options: list[str] = Field(default_factory=list, max_length=12)
    prior_attempts: int = Field(default=0, ge=0, le=10)


class Hint:
    name = "hint"
    description = "A nudge toward the answer without revealing it"
    personalized = False
    input_model = _HintIn

    def build_prompt(self, data: _HintIn, context: dict) -> str:
        options = "\n".join(f"- {o}" for o in data.options)
        return (
            "You are a supportive tutor inside a learning game. Give a short hint "
            "(2 sentences max) that guides the learner toward the answer WITHOUT "
            "stating it or eliminating options outright. "
            f"They have made {data.prior_attempts} prior attempt(s); be slightly more "
            "direct with each attempt.\n"
            f"Question: {data.question}\n"
            + (f"Options:\n{options}" if options else "")
        )


class _ExplanationIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    correct_answer: str = Field(default="", max_length=1000)
    learner_answer: str = Field(default="", max_length=1000)


class Explanation:
    name = "explanation"
    description = "Why an answer is right, addressing the learner's mistake"
    personalized = False
    input_model = _ExplanationIn

    def build_prompt(self, data: _ExplanationIn, context: dict) -> str:
        return (
            "Explain the following exam question clearly in under 120 words. "
            "If the learner's answer differs from the correct one, address the "
            "specific misconception kindly.\n"
            f"Question: {data.question}\n"
            f"Correct answer: {data.correct_answer or 'unspecified'}\n"
            f"Learner answered: {data.learner_answer or 'unspecified'}"
        )


class _FlashcardsIn(BaseModel):
    topic: str = Field(min_length=1, max_length=300)
    count: int = Field(default=8, ge=1, le=20)


class Flashcards:
    name = "flashcards"
    description = "Generate front/back flashcards for a topic"
    personalized = False
    input_model = _FlashcardsIn

    def build_prompt(self, data: _FlashcardsIn, context: dict) -> str:
        return (
            f"Create {data.count} study flashcards on the topic '{data.topic}'. "
            "Respond ONLY with a JSON array of objects with keys 'front' and "
            "'back'. Keep each side under 30 words."
        )


class _NpcDialogueIn(BaseModel):
    npc_name: str = Field(min_length=1, max_length=120)
    npc_role: str = Field(default="", max_length=200)
    situation: str = Field(min_length=1, max_length=1000)
    player_message: str = Field(min_length=1, max_length=1000)


class NpcDialogue:
    name = "npc_dialogue"
    description = "In-character NPC reply for dynamic conversations"
    personalized = False
    input_model = _NpcDialogueIn

    def build_prompt(self, data: _NpcDialogueIn, context: dict) -> str:
        return (
            f"You are {data.npc_name}"
            + (f", {data.npc_role}" if data.npc_role else "")
            + " in an educational adventure game. Stay in character, be concise "
            "(under 60 words), and keep content suitable for all audiences.\n"
            f"Situation: {data.situation}\n"
            f"The player says: {data.player_message}\n"
            "Reply in character:"
        )


class _QuestionGenIn(BaseModel):
    topic: str = Field(min_length=1, max_length=300)
    difficulty: str = Field(default="medium", pattern="^(easy|medium|hard)$")
    count: int = Field(default=5, ge=1, le=10)


class QuestionGeneration:
    name = "question_generation"
    description = "Draft quiz questions for Creator Studio review"
    personalized = False
    input_model = _QuestionGenIn

    def build_prompt(self, data: _QuestionGenIn, context: dict) -> str:
        return (
            f"Draft {data.count} {data.difficulty} multiple-choice questions on "
            f"'{data.topic}'. Respond ONLY with a JSON array matching the platform "
            "quiz config: objects with 'question', 'options' (array of "
            "{'id','text'}), 'correct' (array of option ids), 'explanation'. "
            "These are drafts for human review, not for direct publication."
        )


class _StudyPlanIn(BaseModel):
    certification: str = Field(min_length=1, max_length=200)
    weeks: int = Field(default=4, ge=1, le=12)


class StudyPlan:
    name = "study_plan"
    description = "A weekly plan built around the learner's weak areas"
    personalized = True
    input_model = _StudyPlanIn

    def build_prompt(self, data: _StudyPlanIn, context: dict) -> str:
        return (
            f"Create a {data.weeks}-week study plan for the '{data.certification}' "
            "certification. Prioritize the learner's weakest areas listed below. "
            "Under 250 words, one short paragraph per week.\n"
            f"Learner mastery so far:\n{context.get('progress_summary', 'No history yet.')}"
        )


class _WeaknessIn(BaseModel):
    pass


class WeaknessAnalysis:
    name = "weakness_analysis"
    description = "Identify weak areas and recommend what to replay"
    personalized = True
    input_model = _WeaknessIn

    def build_prompt(self, data: _WeaknessIn, context: dict) -> str:
        return (
            "Given this learner's mission mastery data, identify their 2-3 weakest "
            "areas and recommend which missions to replay and why, in under 120 "
            "words.\n"
            f"Mastery data:\n{context.get('progress_summary', 'No history yet.')}"
        )


class _MissionOutlineIn(BaseModel):
    topic: str = Field(min_length=1, max_length=300)
    nelson_reference: str = Field(default="", max_length=200)
    scene_count: int = Field(default=4, ge=2, le=8)
    challenge_count: int = Field(default=3, ge=1, le=8)


class MissionOutline:
    name = "mission_outline"
    description = "Draft a full mission (scenes, dialogue, challenges) for Creator Studio review"
    personalized = False
    input_model = _MissionOutlineIn

    def build_prompt(self, data: _MissionOutlineIn, context: dict) -> str:
        reference = f" (aligned to {data.nelson_reference})" if data.nelson_reference else ""
        return (
            f"Draft a mission outline on '{data.topic}'{reference} for an educational "
            "adventure game. Respond ONLY with a JSON object matching this game "
            "definition shape: {schema_version: 1, title, description, certification, "
            "start_scene, variables, npcs, items, scenes}. Each scene has a title and "
            "an elements list (types: dialogue, choice, challenge), and either a "
            "'next' scene id or an 'ending' object on terminal scenes. Include roughly "
            f"{data.scene_count} scenes and {data.challenge_count} challenge elements "
            "(challenge_type one of 'quiz', 'ordering', 'text_input', 'matching'), with at least "
            "one branching choice and two distinct endings. "
            "This is a first draft for a human author to review, edit, and validate in "
            "Creator Studio before it is ever submitted or published — do not treat it "
            "as final content."
        )


for _feature in (Hint(), Explanation(), Flashcards(), NpcDialogue(),
                 QuestionGeneration(), StudyPlan(), WeaknessAnalysis(), MissionOutline()):
    register(_feature)
