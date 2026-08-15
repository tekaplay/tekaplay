"""Pure interpreter tests: no DB, no HTTP."""
import json
from pathlib import Path

import pytest

from app.core.errors import ValidationFailedError
from app.modules.runtime import engine
from app.modules.runtime.definition import parse_definition

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "calculus_mission_1.json"


@pytest.fixture
def defn():
    return parse_definition(json.loads(EXAMPLE.read_text()))


def test_definition_validation_catches_broken_graph():
    raw = {
        "schema_version": 1, "title": "Broken", "start_scene": "nope",
        "scenes": {"intro": {"title": "Intro", "elements": [],
                             "next": "missing_scene"}},
    }
    with pytest.raises(ValidationFailedError) as exc_info:
        parse_definition(raw)
    message = str(exc_info.value.details)
    assert "nope" in message and "missing_scene" in message


def test_definition_validation_checks_challenge_configs():
    raw = {
        "schema_version": 1, "title": "Bad quiz", "start_scene": "s",
        "scenes": {"s": {"title": "S", "ending": {"id": "e", "title": "End"},
                         "elements": [{"type": "challenge", "id": "q1",
                                       "challenge_type": "quiz",
                                       "config": {"question": "?", "options": [
                                           {"id": "a", "text": "A"},
                                           {"id": "b", "text": "B"}],
                                           "correct": ["zzz"]}}]}},
    }
    with pytest.raises(ValidationFailedError):
        parse_definition(raw)


def test_new_state_enters_start_scene_and_applies_on_enter(defn):
    state, events = engine.new_state(defn)
    assert state["scene_id"] == "briefing"
    assert "met_priya" in state["flags"]
    assert ("scene.entered", {"scene_id": "briefing"}) in events


def test_view_blocks_on_choice_and_hides_gated_options(defn):
    state, _ = engine.new_state(defn)
    view = engine.compute_view(defn, state)
    assert view.interactive["type"] == "choice"
    assert len(view.passives) == 2  # both intro dialogue lines


def test_view_never_leaks_answers(defn):
    state, _ = engine.new_state(defn)
    engine.choose(defn, state, "first_call", "derivative")
    view = engine.compute_view(defn, state)
    serialized = json.dumps(view.interactive)
    assert view.interactive["type"] == "challenge"
    assert "correct" not in serialized
    assert "explanation" not in serialized


def test_conditional_dialogue_reflects_earlier_choice(defn):
    state, _ = engine.new_state(defn)
    engine.choose(defn, state, "first_call", "average_only")
    view = engine.compute_view(defn, state)
    texts = " ".join(p["text"] for p in view.passives)
    assert "Averages hide" in texts and "Good instinct" not in texts


def test_answer_wrong_then_right_and_attempt_tracking(defn):
    state, _ = engine.new_state(defn)
    engine.choose(defn, state, "first_call", "derivative")
    result, events = engine.answer(defn, state, "q_derivative_def", {"selected": ["b"]})
    assert not result.correct
    assert state["variables"]["mastery"] == 1  # +2 choice, -1 wrong
    result, events = engine.answer(defn, state, "q_derivative_def", {"selected": ["a"]})
    assert result.correct
    assert "instantaneous rate of change" in result.feedback
    names = [n for n, _ in events]
    assert "question.answered" in names and "xp.awarded" in names


def test_attempts_exhausted_moves_on(defn):
    state, _ = engine.new_state(defn)
    engine.choose(defn, state, "first_call", "derivative")
    engine.answer(defn, state, "q_derivative_def", {"selected": ["b"]})
    engine.answer(defn, state, "q_derivative_def", {"selected": ["c"]})  # 2/2 attempts
    view = engine.compute_view(defn, state)
    assert view.interactive["id"] == "q_order"  # moved past the failed quiz
    assert state["results"]["q_derivative_def"]["correct"] is False


def test_full_playthrough_to_hero_ending(defn):
    state, _ = engine.new_state(defn)
    engine.choose(defn, state, "first_call", "derivative")
    engine.answer(defn, state, "q_derivative_def", {"selected": ["a"]})
    engine.answer(defn, state, "q_order", {"order": ["position", "velocity", "acceleration"]})
    engine.answer(defn, state, "q_power_rule", {"text": " 6T "})
    assert state["inventory"]["graph_pad"] == 1
    engine.advance(defn, state)  # rate_check → debrief
    events = engine.choose(defn, state, "wrap_up", "hero_end")
    assert ("achievement.unlocked", {"code": "rate_of_change_hero"}) in [
        (n, p) for n, p in events if n == "achievement.unlocked"
    ]
    events = engine.advance(defn, state)  # ending scene → finish
    assert state["status"] == engine.STATUS_COMPLETED
    assert state["ending_id"] == "hero"
    finished = [p for n, p in events if n == "mission.finished"][0]
    assert finished["xp_earned"] == 180  # 50+40+30+60


def test_low_reputation_gates_hero_option(defn):
    state, _ = engine.new_state(defn)
    engine.choose(defn, state, "first_call", "average_only")  # mastery -1
    engine.answer(defn, state, "q_derivative_def", {"selected": ["a"]})  # mastery 0
    engine.answer(defn, state, "q_order", {"order": ["position", "velocity", "acceleration"]})
    engine.answer(defn, state, "q_power_rule", {"text": "6t"})
    engine.advance(defn, state)
    view = engine.compute_view(defn, state)
    option_ids = [o["id"] for o in view.interactive["options"]]
    assert option_ids == ["quiet_end"]  # hero_end hidden by condition
    with pytest.raises(ValidationFailedError):
        engine.choose(defn, state, "wrap_up", "hero_end")


def test_advance_refused_while_interaction_pending(defn):
    state, _ = engine.new_state(defn)
    with pytest.raises(ValidationFailedError):
        engine.advance(defn, state)
