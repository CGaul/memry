"""The topic pick (``retrieval.topic_pick``, ``MemoryStore._picked_entities``):
for a question that needs several memories, one more call of the decision
provider reads the tags and named things of the scope and picks those the
answer is about; their memories come first in the set call's pool
(``MemoryStore._set_pool``). Off by default."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from memry.config import Config
from memry.models import Entity, Scope
from memry.providers.llm import NoneLLM
from memry.store import MemoryStore

from memry.providers.embeddings import Embedder
from test_set_questions import _add, _Batches

QUESTION = "Which cities has Dave travelled to?"


class _TripEmbedder(Embedder):
    """A trip, a bug and anything said of Detroit point three ways: Detroit
    the opposite of a trip, so it is never the nearest to the trips found."""

    name, _model, dimensions = "trip", "v1", 3

    def embed(self, texts):
        def vector(text):
            if "Detroit" in text:
                return [-1.0, 0.0, 0.1]
            if "travel" in text:
                return [1.0, 0.0, 0.1]
            return [0.0, 1.0, 0.1]
        return [vector(t) for t in texts]


@pytest.fixture
def store():
    s = MemoryStore(Config(db_path=":memory:"), llm=NoneLLM(), embedder=_TripEmbedder())
    s.config.retrieval.relational_relevance = "jev"
    s.config.retrieval.set_pool = 10
    yield s
    s.close()


def _place(store, name):
    return store.backend.insert_entity(Entity(name=name, normalized=name.lower(),
                                              entity_type="place", user_id="ada"))


def _trips(store):
    """20 trips the question's words find, filed under "travel", and one trip
    told as work: no word of the question, the tag "work", and the place
    Detroit."""
    trips = [_add(store, f"Dave travelled to city{i} for a week.", ("travel",))
             for i in range(20)]
    detroit = _place(store, "Detroit")
    work = _add(store, "Dave gave a talk at a conference in Detroit.", ("work",), [detroit])
    for i in range(15):
        _add(store, f"Dave fixed bug {i} in the build.", ("work",))
    return trips, work


def _judge():
    """Every trip is a member; the pick takes the places."""
    return _Batches(specific=0.9, several=0.9,
                    scores={"(place)": 0.9, "travelled": 0.12, "conference in": 0.12})


def test_off_by_default_the_set_call_reads_the_shared_topic_only(store):
    trips, work = _trips(store)
    assert store.config.retrieval.topic_pick is False
    store.decider = judge = _judge()
    results = store.search(QUESTION, user_id="ada", limit=5)
    assert judge.calls == 2
    assert work.id not in {r.memory.id for r in results}


def test_the_picked_place_brings_its_memory_into_the_set(store):
    trips, work = _trips(store)
    store.config.retrieval.topic_pick = True
    store.decider = judge = _judge()
    results = store.search(QUESTION, user_id="ada", limit=5)
    assert judge.calls == 3  # the first call, the pick, the set call
    first, pick, second = judge.batches
    assert pick == []  # the pick asks about tags and things, no memory
    assert work.content in second  # the picked memory takes a place first
    members = {r.memory.id for r in results if r.signals.get("member")}
    assert work.id in members and {m.id for m in trips} <= members
    assert results[0].signals["calls"] == 3


def test_the_pick_reads_tags_and_named_things_with_their_kind(store):
    _trips(store)
    store.config.retrieval.topic_pick = True
    asked: list[list[str]] = []
    judge = _judge()
    decide = judge.decide

    def recording(state, questions):
        if any(key.startswith("e") for key in questions):
            asked.append([q.instructions for q in questions.values()])
        return decide(state, questions)

    judge.decide = recording
    store.decider = judge
    store.search(QUESTION, user_id="ada", limit=5)
    (texts,) = asked
    assert "Some of the memories that answer the question are about Detroit (place)." in texts
    assert 'Some of the memories that answer the question are about the topic "work".' in texts
    store.config.retrieval.topic_pick_pool = 2
    asked.clear()
    store.search(QUESTION, user_id="ada", limit=5)
    assert len(asked[0]) == 2


def test_a_one_answer_question_makes_no_pick(store):
    _trips(store)
    store.config.retrieval.topic_pick = True
    store.decider = judge = _Batches(specific=0.9, several=0.1, scores={"travelled": 0.9})
    store.search("Where did Dave travel in week one?", user_id="ada", limit=5)
    assert judge.calls == 1


def test_picked_memories_take_the_budget_first(store):
    trips, work = _trips(store)
    for i in range(12):
        _add(store, f"Dave took the train through Detroit, stop {i}.", ("work",),
             [store.backend.find_entities("detroit", Scope(user_id="ada"))[0]])
    store.config.retrieval.topic_pick = True
    store.decider = judge = _judge()
    store.search(QUESTION, user_id="ada", limit=5)
    second = judge.batches[-1]
    assert len(second) == 10  # the budget
    assert all("Detroit" in text for text in second)  # no place left for the topic walk
