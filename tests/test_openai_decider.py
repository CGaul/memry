"""OpenAI's Decisions API behind Memry's decider interface.

Every test drives an httpx mock transport: no test reaches the network. What
they pin is the request Memry sends (``POST /v1/decisions``, one question per
Memry question, typed) and how the reply is read back into the answers Jev
gives, so every call site reads either provider unchanged.
"""

from __future__ import annotations

import json

import httpx
import pytest

from conftest import FakeLLM
from memry.config import Config, DecisionConfig, model_requirements
from memry.intelligence.identity import BELONGS_QUESTION, PAIR_QUESTION
from memry.providers.decisions import (
    NEVER_AUTO_MERGE,
    Choice,
    Noul,
    OpenAIDecider,
    Score,
    build_decider,
)

QUESTIONS = {
    "identity": Choice(instructions="same person?",
                       criteria={"same": "clearly", "different": "clearly not",
                                 "unsure": "cannot tell"}),
    "urgency": Score(instructions="how urgent", levels=["low", "medium", "high"]),
    "is_question": Noul(instructions="the state asks a question"),
}


def openai(handler, **cfg) -> OpenAIDecider:
    """An OpenAIDecider whose HTTP client is backed by ``handler``."""
    return OpenAIDecider(DecisionConfig(provider="openai", api_key="k", **cfg),
                         transport=httpx.MockTransport(handler))


def reply(answers: list, **extra) -> httpx.Response:
    return httpx.Response(200, json={"model": "gpt-6-luna", "answers": answers,
                                     "usage": {"input_tokens": 42, "output_tokens": 0},
                                     **extra})


# ---------------------------------------------------------------- the request
def test_one_post_to_decisions_carries_every_question_typed():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return reply([])

    openai(handler).decide("Ada lives in Amsterdam", QUESTIONS)

    assert seen["url"] == "https://api.openai.com/v1/decisions"
    assert seen["auth"] == "Bearer k"
    assert seen["body"] == {
        "model": "gpt-6-luna",
        "input": "Ada lives in Amsterdam",
        "questions": [
            {"type": "choice", "name": "identity", "instructions": "same person?",
             "choices": [{"value": "same", "description": "clearly"},
                         {"value": "different", "description": "clearly not"},
                         {"value": "unsure", "description": "cannot tell"}]},
            {"type": "score", "name": "urgency", "instructions": "how urgent",
             "levels": [{"label": "low"}, {"label": "medium"}, {"label": "high"}]},
            {"type": "predicate", "name": "is_question",
             "instructions": "the state asks a question"},
        ],
    }


def test_the_pair_and_belongs_questions_go_out_as_two_choices_in_one_call():
    bodies: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return reply([])

    openai(handler).decide("ENTITY A ... ENTITY B ...",
                           {"pair": PAIR_QUESTION, "belongs": BELONGS_QUESTION})

    assert len(bodies) == 1
    pair, belongs = bodies[0]["questions"]
    assert [c["value"] for c in pair["choices"]] == ["same", "different", "unsure"]
    assert pair["choices"][0]["description"] == PAIR_QUESTION.criteria["same"]
    assert [c["value"] for c in belongs["choices"]] == list(BELONGS_QUESTION.criteria)


def test_a_choice_the_api_cannot_take_is_left_out_and_abstains():
    """The API takes 2 to 255 choices. A question outside that is not sent;
    the others still are."""
    bodies: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return reply([{"type": "predicate", "name": "ok", "probability": 0.7}])

    questions = {
        "one": Choice(instructions="only one option", criteria={"a": "the only one"}),
        "many": Choice(instructions="too many", criteria={str(i): "x" for i in range(256)}),
        "ok": Noul(instructions="fine"),
    }
    answers = openai(handler).decide("state", questions)

    assert [q["name"] for q in bodies[0]["questions"]] == ["ok"]
    assert answers["one"].available is False
    assert answers["many"].available is False
    assert answers["ok"].value == pytest.approx(0.7)


def test_the_model_and_base_url_are_configurable():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["model"] = json.loads(request.content)["model"]
        return reply([])

    openai(handler, model="gpt-6-luna-2026-10-01",
           base_url="https://proxy.example/v1/").decide("s", QUESTIONS)
    assert seen == {"url": "https://proxy.example/v1/decisions",
                    "model": "gpt-6-luna-2026-10-01"}


# ---------------------------------------------------------------- the reply
def test_each_answer_type_comes_back_in_the_shape_jev_gives():
    def handler(_request: httpx.Request) -> httpx.Response:
        return reply([
            {"type": "choice", "name": "identity", "choice": "same",
             "probabilities": [{"value": "same", "probability": 0.94},
                               {"value": "different", "probability": 0.04},
                               {"value": "unsure", "probability": 0.02}],
             "confidence": 0.9},
            {"type": "score", "name": "urgency", "score": 1.1,
             "probabilities": [{"value": 0, "label": "low", "probability": 0.1},
                               {"value": 1, "label": "medium", "probability": 0.7},
                               {"value": 2, "label": "high", "probability": 0.2}],
             "confidence": 0.55},
            {"type": "predicate", "name": "is_question", "probability": 0.12},
        ])

    decider = openai(handler)
    answers = decider.decide("state", QUESTIONS)

    identity = answers["identity"]
    assert identity.available and identity.value == "same"
    assert identity.probabilities == pytest.approx(
        {"same": 0.94, "different": 0.04, "unsure": 0.02})
    assert identity.confidence == pytest.approx(0.9)
    urgency = answers["urgency"]
    assert urgency.value == pytest.approx(1.1)
    # keyed by level index as a string, as Jev keys a score
    assert urgency.probabilities == pytest.approx({"0": 0.1, "1": 0.7, "2": 0.2})
    assert urgency.confidence == pytest.approx(0.55)
    truth = answers["is_question"]
    assert truth.value == pytest.approx(0.12)
    assert truth.probabilities == {}
    # as for a Jev noul: distance from a coin flip
    assert truth.confidence == pytest.approx(0.76)
    assert decider.served_model == "gpt-6-luna"
    assert decider.input_tokens == 42


def test_answers_without_a_name_are_read_in_the_order_asked():
    def handler(_request: httpx.Request) -> httpx.Response:
        return reply([
            {"type": "choice", "name": None, "choice": "unsure",
             "probabilities": [{"value": "unsure", "probability": 0.6}], "confidence": 0.3},
            {"type": "score", "name": None, "score": 2, "probabilities": [], "confidence": 0.8},
            {"type": "predicate", "name": None, "probability": 0.5},
        ])

    answers = openai(handler).decide("state", QUESTIONS)
    assert answers["identity"].value == "unsure"
    assert answers["urgency"].value == 2
    assert answers["is_question"].value == pytest.approx(0.5)


def test_a_choice_without_its_confidence_takes_the_probability_of_the_choice():
    def handler(_request: httpx.Request) -> httpx.Response:
        return reply([{"type": "choice", "name": "identity", "choice": "different",
                       "probabilities": [{"value": "same", "probability": 0.3},
                                         {"value": "different", "probability": 0.65}]}])

    answer = openai(handler).decide("state", {"identity": QUESTIONS["identity"]})["identity"]
    assert answer.confidence == pytest.approx(0.65)


def test_a_refusal_abstains_for_that_question_only():
    def handler(_request: httpx.Request) -> httpx.Response:
        return reply([
            {"type": "refusal", "name": "identity"},
            {"type": "predicate", "name": "is_question", "probability": 0.9},
        ])

    answers = openai(handler).decide("state", QUESTIONS)
    assert answers["identity"].available is False
    assert answers["urgency"].available is False  # not answered at all
    assert answers["is_question"].value == pytest.approx(0.9)


@pytest.mark.parametrize("answer", [
    {"type": "choice", "name": "identity", "choice": "maybe", "probabilities": []},
    {"type": "predicate", "name": "identity", "probability": 0.4},  # wrong type
    {"type": "score", "name": "urgency", "score": 3.5, "probabilities": []},  # off the rubric
    {"type": "predicate", "name": "is_question", "probability": "high"},
], ids=["unknown choice", "wrong type", "score off the rubric", "probability not a number"])
def test_an_answer_outside_the_question_abstains(answer):
    def handler(_request: httpx.Request) -> httpx.Response:
        return reply([answer])

    assert openai(handler).decide("state", QUESTIONS)[answer["name"]].available is False


@pytest.mark.parametrize("handler", [
    lambda r: httpx.Response(500, text="boom"),
    lambda r: httpx.Response(429, json={"error": {"message": "rate limited"}}),
    lambda r: httpx.Response(200, text="not json"),
    lambda r: httpx.Response(200, json={"answers": {"identity": {}}}),  # not a list
], ids=["server error", "rate limit", "not json", "answers not a list"])
def test_every_failure_abstains_rather_than_raising(handler):
    answers = openai(handler).decide("state", QUESTIONS)
    assert all(not answers[k].available for k in QUESTIONS)


def test_a_transport_error_abstains():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    assert openai(handler).decide("state", QUESTIONS)["identity"].available is False


# ---------------------------------------------------------------- selection
def test_without_a_key_it_abstains_and_sends_nothing(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    sent: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return reply([])

    decider = OpenAIDecider(DecisionConfig(provider="openai"),
                            transport=httpx.MockTransport(handler))
    assert decider.available is False
    assert decider.decide("state", QUESTIONS)["identity"].available is False
    assert sent == []


def test_the_openai_key_of_the_text_model_is_used_when_no_decision_key_is_set(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-text")
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return reply([])

    decider = OpenAIDecider(DecisionConfig(provider="openai"),
                            transport=httpx.MockTransport(handler))
    decider.decide("state", QUESTIONS)
    assert seen["auth"] == "Bearer sk-text"


def test_build_decider_picks_openai_from_the_config():
    decider = build_decider(DecisionConfig(provider="openai", api_key="k"), FakeLLM())
    assert isinstance(decider, OpenAIDecider)
    assert decider.name == "openai"
    decider.close()


@pytest.mark.parametrize("variable", ["MEMRY_DECIDER", "MEMRY_DECISION_PROVIDER"])
def test_the_environment_selects_openai(monkeypatch, tmp_path, variable):
    monkeypatch.delenv("MEMRY_DECISION_PROVIDER", raising=False)
    monkeypatch.delenv("MEMRY_DECIDER", raising=False)
    monkeypatch.setenv("MEMRY_CONFIG", str(tmp_path / "none.json"))
    monkeypatch.setenv(variable, "openai")
    assert Config.load().decision.provider == "openai"


def test_the_long_variable_wins_over_the_short_one(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMRY_CONFIG", str(tmp_path / "none.json"))
    monkeypatch.setenv("MEMRY_DECISION_PROVIDER", "jev")
    monkeypatch.setenv("MEMRY_DECIDER", "openai")
    assert Config.load().decision.provider == "jev"


def test_a_server_needs_a_key_for_openai(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = Config(decision=DecisionConfig(provider="openai"))
    assert any("MEMRY_DECISION_PROVIDER=openai" in item for item in model_requirements(cfg))
    cfg = Config(decision=DecisionConfig(provider="openai", api_key="k"))
    assert not any("openai" in item.lower() and "decision" in item.lower()
                   for item in model_requirements(cfg))


def test_nothing_is_trusted_until_measured():
    """The Decisions page makes no claim about calibration. Until Memry
    measures it against labelled answers, this provider merges nothing on its
    own, sets no reconcile bars and judges no search by default."""
    decider = OpenAIDecider(DecisionConfig(provider="openai", api_key="k"))
    assert decider.calibrated is False
    assert decider.pair_merge_probability == NEVER_AUTO_MERGE
    assert decider.pair_merge_threshold(50) == NEVER_AUTO_MERGE
    assert decider.tag_merge_probability == NEVER_AUTO_MERGE
    assert decider.auto_confirm_confidence == NEVER_AUTO_MERGE
    assert decider.reconcile_bars is None
    assert decider.reranks_by_default is False
    assert decider.may_rerank is False
    assert decider.rejudges_on_new_evidence is False
    # a deployment's pair bar applies only to a calibrated judge
    built = build_decider(DecisionConfig(provider="openai", api_key="k",
                                         pair_merge_probability=0.9), FakeLLM())
    assert built.pair_merge_probability == NEVER_AUTO_MERGE
    decider.close()
    built.close()
