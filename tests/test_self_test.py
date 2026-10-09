"""The nightly self-test (``upkeep.self_test``): each memory's own question
keys are searched without the judge and without the keys, and where the
memory ranks is kept. A memory every question of which ranks it past the
depth is hard to find; another memory first for a memory's question is a
duplicate candidate."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

from memry.config import Config, DecisionConfig
from memry.models import Memory
from memry.providers.decisions import JevDecider
from memry.providers.embeddings import Embedder
from memry.providers.llm import NoneLLM
from memry.store import MemoryStore


class _ScriptedEmbedder(Embedder):
    """One dimension per group of words, so a test decides which texts are
    near: "based" is near "based" only, "resides" near "resides"."""

    name, _model, dimensions = "scripted", "v1", 5
    GROUPS = [{"based"}, {"resides", "reside"}, {"tea", "drink", "drinks"}, {"floor", "high"}]

    def embed(self, texts):
        out = []
        for text in texts:
            words = set(re.findall(r"[a-z]+", text.lower()))
            out.append([float(len(words & g)) for g in self.GROUPS] + [0.1])
        return out


def _store(*, self_test: bool = True, question_keys: bool = True, decider=None) -> MemoryStore:
    cfg = Config(db_path=":memory:")
    cfg.retrieval.question_keys = question_keys
    cfg.upkeep.self_test = self_test
    return MemoryStore(cfg, llm=NoneLLM(), embedder=_ScriptedEmbedder(), decider=decider)


def _add(store: MemoryStore, text: str, questions: list[str], user_id: str = "ada") -> str:
    memory = store.backend.insert_memory(
        Memory(content=text, user_id=user_id, embedding_model=store.embedder.model_id),
        embedding=store.embedder.embed([text])[0])
    if questions:
        store._write_questions(memory.id, questions, "backfill")
    return memory.id


@pytest.fixture
def world():
    """Two wordings of where Ada lives, each asked in the other's words, a
    fact asked in its own words, and a fact asked in none of them."""
    store = _store()
    ids = {
        "resides": _add(store, "Ada resides in Lisbon.", ["Where is Ada based?"]),
        "based": _add(store, "Ada is based in Lisbon.", ["Which city does Ada reside in?"]),
        "tea": _add(store, "Ada drinks green tea.", ["What tea does Ada drink?",
                                                     "Which drink does Ada like?"]),
        "floor": _add(store, "The flat is on the fourth floor.", ["How high up is the home?"]),
    }
    yield store, ids
    store.close()


def _by_memory(store):
    from memry.models import Scope
    return {row["memory_id"]: row for row in store.backend.self_test_rows(Scope(user_id="ada"))}


def test_a_memory_asked_in_its_own_words_ranks_first(world):
    store, ids = world
    rows = store.self_test_memories([store.backend.get_memory(ids["tea"])])
    assert len(rows) == 1
    row = rows[0]
    assert row["rank"] == 1
    assert row["question"] == "What tea does Ada drink?"
    assert [f[1] for f in row["firsts"]] == [1, 1]
    assert {f[2] for f in row["firsts"]} == {ids["tea"]}
    assert _by_memory(store)[ids["tea"]]["rank"] == 1


def test_another_wording_first_is_a_mutual_duplicate_candidate(world):
    store, ids = world
    store.self_test_memories([store.backend.get_memory(i) for i in ids.values()])
    report = store.self_test_report(user_id="ada")
    assert report["tested"] == 4
    pair = {ids["resides"], ids["based"]}
    mutual = [c for c in report["duplicate_candidates"] if c["mutual"]]
    assert [{c["memory_id"], c["other_id"]} for c in mutual] == [pair]
    assert mutual[0]["questions"] == mutual[0]["of"] == 1
    # the pair once, and first in the list
    assert report["duplicate_candidates"][0] is mutual[0]
    assert sum({c["memory_id"], c["other_id"]} == pair
               for c in report["duplicate_candidates"]) == 1
    assert ids["tea"] not in {c["memory_id"] for c in report["duplicate_candidates"]}


def test_a_memory_past_the_depth_for_every_question_is_hard_to_find(world):
    store, ids = world
    memories = [store.backend.get_memory(i) for i in ids.values()]
    store.self_test_memories(memories)
    assert store.self_test_report(user_id="ada")["hard_to_find"] == 0
    store.self_test_memories(memories, depth=1)
    report = store.self_test_report(user_id="ada")
    assert set(report["hard_to_find_ids"]) == {ids["resides"], ids["based"]}
    assert report["hard_to_find"] == 2
    row = _by_memory(store)[ids["resides"]]
    assert row["rank"] is None
    assert row["question"] == "Where is Ada based?"  # the first, when none ranks it


def test_the_own_question_keys_are_left_out(world):
    """Compared with itself, a question ranks its own memory first: with the
    keys in, the memory asked in other words is returned first."""
    store, ids = world
    memory = store.backend.get_memory(ids["resides"])
    without = store.self_test_memories([memory], record=False)[0]
    with_keys = store.self_test_memories([memory], record=False, question_keys=True)[0]
    assert without["firsts"][0][2] == ids["based"]
    assert with_keys["firsts"][0][2] == ids["resides"]


def test_the_self_test_asks_no_judge():
    calls = []
    decider = JevDecider(DecisionConfig(provider="jev", api_key="k"))
    decider.decide = lambda *a, **k: calls.append(a) or {}
    store = _store(decider=decider)
    store.config.retrieval.relational_relevance = "jev"
    try:
        mid = _add(store, "Ada drinks green tea.", ["What tea does Ada drink?"])
        rows = store.self_test_memories([store.backend.get_memory(mid)])
        assert rows[0]["rank"] == 1
        assert calls == []
    finally:
        store.close()


def test_new_questions_or_a_deleted_memory_drop_the_result(world):
    store, ids = world
    store.self_test_memories([store.backend.get_memory(i) for i in ids.values()])
    store._write_questions(ids["tea"], ["Is Ada a tea person?"], "agent")
    assert ids["tea"] not in _by_memory(store)
    store.backend.delete_memory(ids["floor"])
    assert ids["floor"] not in _by_memory(store)
    assert set(_by_memory(store)) == {ids["resides"], ids["based"]}


def test_a_memory_without_questions_is_not_tested():
    store = _store()
    try:
        mid = _add(store, "Ada drinks green tea.", [])
        assert store.self_test_memories([store.backend.get_memory(mid)]) == []
        assert store.self_test_report(user_id="ada")["tested"] == 0
    finally:
        store.close()


def test_the_pass_is_off_without_its_switch_or_the_question_keys():
    for self_test, keys in ((False, True), (True, False)):
        store = _store(self_test=self_test, question_keys=keys)
        try:
            _add(store, "Ada drinks green tea.", ["What tea does Ada drink?"])
            assert not store.maintenance_enabled("self_test")
            assert store.set_maintenance_enabled("self_test", True)
            assert not store.maintenance_enabled("self_test")  # a toggle cannot turn it on
            assert store.run_upkeep_pass("self_test", user_id="ada")["ran"] is False
            assert "self_test" not in store.run_upkeep_cycle(user_id="ada")
            assert store.self_test_report(user_id="ada")["tested"] == 0
        finally:
            store.close()


def test_the_upkeep_cycle_tests_each_memory_once_a_day(world):
    store, ids = world
    now = datetime(2026, 10, 9, 3, 0, tzinfo=timezone.utc)
    ran = store.run_upkeep_cycle(user_id="ada", now=now)
    assert ran["self_test"]["tested"] == 4
    assert ran["self_test"]["searches"] == 5
    assert ran["self_test"]["hard_to_find"] == 0
    assert ran["self_test"]["duplicate_candidates"] >= 1
    assert store.last_pass_run("self_test", "ada")["result"]["tested"] == 4
    # an hour later nothing is due: no search, no run
    assert "self_test" not in store.run_upkeep_cycle(user_id="ada",
                                                     now=now + timedelta(hours=1))
    # a memory saved since is tested on the next tick alone
    _add(store, "Ada drinks black tea at work.", ["What does Ada drink at work?"])
    ran = store.run_upkeep_cycle(user_id="ada", now=now + timedelta(hours=2))
    assert ran["self_test"]["tested"] == 1


def test_a_day_later_every_memory_is_due_again(world):
    store, ids = world
    store.self_test_memories([store.backend.get_memory(i) for i in ids.values()])
    stamp = _by_memory(store)[ids["tea"]]["tested_at"]
    later = datetime.fromisoformat(stamp) + timedelta(days=1, minutes=1)
    assert store.run_self_test(user_id="ada", now=later)["tested"] == 4


def test_another_users_memories_are_neither_tested_nor_returned(world):
    store, ids = world
    other = _add(store, "Bo is based in Porto.", ["Where is Bo based?"], user_id="bo")
    store.run_self_test(user_id="ada")
    assert other not in _by_memory(store)
    firsts = {f[2] for row in _by_memory(store).values() for f in row["firsts"]}
    assert other not in firsts


def test_the_switch_reads_from_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMRY_CONFIG", str(tmp_path / "none.json"))
    monkeypatch.setenv("MEMRY_SELF_TEST", "1")
    assert Config.load().upkeep.self_test is True
    monkeypatch.delenv("MEMRY_SELF_TEST")
    assert Config().upkeep.self_test is False
