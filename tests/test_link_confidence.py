"""Link confidence (``retrieval.link_confidence``): a relation counts in the
linked search by its confirmations. Scripted stores. A relation contained in
more memories counts more. One not confirmed for long falls to the floor.
With the flag off nothing changes."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from memry.config import Config
from memry.intelligence.graph_retrieval import (
    CONFIDENCE_CAP,
    CONFIDENCE_FLOOR,
    LINKED_RELATION,
    LOW,
    relation_confidence,
)
from memry.models import Entity, EntityMention, Memory, Relation, Scope
from memry.providers.embeddings import HashEmbedder
from memry.providers.llm import NoneLLM
from memry.store import MemoryStore

NOW = "2026-10-09T12:00:00+00:00"


def _ago(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


def test_the_formula_at_its_landmarks():
    assert relation_confidence(1, NOW, 0, NOW) == 1.0  # today's weight
    assert relation_confidence(1, "2026-09-15T12:00:00+00:00", 0, NOW) == 1.0  # in the grace
    assert relation_confidence(3, NOW, 0, NOW) == pytest.approx(1.3296, abs=1e-4)
    assert relation_confidence(5, NOW, 0, NOW) == pytest.approx(1.4828, abs=1e-4)
    assert relation_confidence(1000, NOW, 0, NOW) == CONFIDENCE_CAP
    # 210 days: 180 past the grace, one half-life
    assert relation_confidence(3, "2026-03-13T12:00:00+00:00", 0, NOW) == pytest.approx(
        1.3296 / 2, abs=1e-3)
    assert relation_confidence(1, "2025-09-04T12:00:00+00:00", 0, NOW) == CONFIDENCE_FLOOR
    assert relation_confidence(5, NOW, 1, NOW) == pytest.approx(1.4828 / 2, abs=1e-4)
    assert relation_confidence(1, None, 0, NOW) == 1.0
    assert LINKED_RELATION * CONFIDENCE_FLOOR == pytest.approx(LOW)


@pytest.fixture
def store():
    s = MemoryStore(Config(db_path=":memory:"), llm=NoneLLM(), embedder=HashEmbedder(96))
    yield s
    s.close()


def _entity(store, name):
    return store.backend.insert_entity(Entity(name=name, normalized=name.lower(), user_id="ada"))


def _memory(store, content, entities):
    m = store.backend.insert_memory(Memory(content=content, user_id="ada"),
                                    embedding=store.embedder.embed([content])[0])
    for e in entities:
        store.backend.add_mention(EntityMention(entity_id=e.id, memory_id=m.id, surface=e.name))
    return m


@pytest.fixture
def world(store):
    """Ada works on Helios (said in three memories) and on Orion (said in
    one). Both projects use Postgres, in the same words, so only the links
    tell the two tool memories apart: with the names read "it", both read
    "It uses the it tool in production"."""
    ada, helios, orion = (_entity(store, n) for n in ("Ada", "Helios", "Orion"))
    postgres = _entity(store, "Postgres")
    for text in ("Ada works on Helios.", "Ada is on the Helios team.", "Ada still works on Helios."):
        m = _memory(store, text, [ada, helios])
        store.backend.add_relation(Relation(subject=ada.id, predicate="works_on",
                                            object=helios.id, user_id="ada", memory_id=m.id))
    m = _memory(store, "Ada works on Orion.", [ada, orion])
    store.backend.add_relation(Relation(subject=ada.id, predicate="works_on", object=orion.id,
                                        user_id="ada", memory_id=m.id))
    tools = {
        "helios": _memory(store, "Helios uses the Postgres tool in production.", [helios, postgres]),
        "orion": _memory(store, "Orion uses the Postgres tool in production.", [orion, postgres]),
    }
    for i in range(30):
        _memory(store, f"Unrelated note number {i} about sprint planning.", [])
    store.refresh_property_vectors(user_id="ada")  # the names read "it", as a save does
    return {"ada": ada, "helios": helios, "orion": orion, "tools": tools}


def _about(store, flag, query="What tool does Ada use in production?"):
    store.config.retrieval.link_confidence = flag
    store.config.retrieval.relational_relevance = "vector"
    results = store.search(query, user_id="ada", limit=20)
    return results, {r.memory.id: r.signals.get("about") for r in results}


def test_three_confirmations_are_counted_once_each(store, world):
    rows = {r.object: r for r in store.backend.relations_of([world["ada"].id])}
    assert rows[world["helios"].id].confirmations == 3
    assert rows[world["orion"].id].confirmations == 1
    # the same memory stating it again is no new confirmation
    first = rows[world["helios"].id]
    store.backend.add_relation(Relation(subject=first.subject, predicate="works_on",
                                        object=first.object, user_id="ada",
                                        memory_id=first.memory_id))
    again = {r.object: r for r in store.backend.relations_of([world["ada"].id])}
    assert again[world["helios"].id].confirmations == 3


def test_a_relation_confirmed_by_three_memories_outranks_one_confirmed_by_one(store, world):
    helios_tool, orion_tool = world["tools"]["helios"].id, world["tools"]["orion"].id
    results, about = _about(store, True)
    assert about[helios_tool] == pytest.approx(LINKED_RELATION * 1.3296, abs=1e-3)
    assert about[orion_tool] == LINKED_RELATION
    order = [r.memory.id for r in results]
    assert order.index(helios_tool) < order.index(orion_tool)
    # off, the two count alike
    _, about = _about(store, False)
    assert about[helios_tool] == about[orion_tool] == LINKED_RELATION


def test_an_old_unconfirmed_relation_falls_to_the_floor(store, world):
    store.backend._db.execute("UPDATE relations SET confirmed_at = ? WHERE object = ?",
                              (_ago(400), world["orion"].id))
    _, about = _about(store, True)
    assert about[world["tools"]["orion"].id] == pytest.approx(LOW)
    # a little younger, it is on its way down
    store.backend._db.execute("UPDATE relations SET confirmed_at = ? WHERE object = ?",
                              (_ago(120), world["orion"].id))
    _, about = _about(store, True)
    assert LOW < about[world["tools"]["orion"].id] < LINKED_RELATION


def test_the_flag_off_changes_nothing(store, world):
    def run():
        results = _about(store, False)[0]
        # the scores carry recency on the wall clock, which moves between calls
        return [(r.memory.id, r.signals.get("about")) for r in results], [r.score for r in results]

    order, scores = run()
    store.backend._db.execute(
        "UPDATE relations SET confirmations = 9, confirmed_at = ?, contradictions = 2",
        (_ago(900),))
    order_after, scores_after = run()
    assert order == order_after
    assert scores == pytest.approx(scores_after, rel=1e-6)


def test_restating_and_contradicting_a_memory_count_on_its_relations(store, world):
    orion_said = next(r for r in store.backend.relations_of([world["ada"].id])
                      if r.object == world["orion"].id)
    assert store.backend.confirm_relations(orion_said.memory_id, NOW) == 1
    assert store.backend.contradict_relations(orion_said.memory_id) == 1
    row = next(r for r in store.backend.relations_of([world["ada"].id])
               if r.object == world["orion"].id)
    assert (row.confirmations, row.confirmed_at, row.contradictions) == (2, NOW, 1)
    # an earlier restatement does not move the last confirmation back
    store.backend.confirm_relations(orion_said.memory_id, "2020-01-01T00:00:00+00:00")
    row = next(r for r in store.backend.relations_of([world["ada"].id])
               if r.object == world["orion"].id)
    assert (row.confirmations, row.confirmed_at) == (3, NOW)


def test_a_store_from_before_gets_the_columns(tmp_path):
    path = tmp_path / "old.db"
    s = MemoryStore(Config(db_path=str(path)), llm=NoneLLM(), embedder=HashEmbedder(32))
    a, b = (s.backend.insert_entity(Entity(name=n, normalized=n.lower(), user_id="ada"))
            for n in ("Ada", "Helios"))
    s.backend.add_relation(Relation(subject=a.id, predicate="works_on", object=b.id,
                                    user_id="ada"))
    s.close()
    db = sqlite3.connect(path)
    for column in ("confirmations", "confirmed_at", "contradictions"):
        db.execute(f"ALTER TABLE relations DROP COLUMN {column}")
    db.commit()
    db.close()
    s = MemoryStore(Config(db_path=str(path)), llm=NoneLLM(), embedder=HashEmbedder(32))
    (row,) = s.backend.relations_of([a.id])
    assert (row.confirmations, row.confirmed_at, row.contradictions) == (1, None, 0)
    assert s.relations(user_id="ada")
    s.close()
    assert Scope  # imported for the fixture helpers' types
