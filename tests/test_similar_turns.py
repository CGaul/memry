"""The turns most similar to the question (``MemoryStore.similar_turns``):
up to ``retrieval.similar_turns`` passages of the scope, found by their words
and their vectors on every context and with no judge, shown with turn
search's turns in the order said. They take only what the rest of the
context leaves, so they never push a memory out. Off by default."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from memry.intelligence.context import estimate_tokens

from evals import external_benchmarks as xb
from test_passages import _ASKED, _save_long, _store
from test_turn_search import _fact


def _context(store, budget=4000, query=_ASKED):
    results = store.search(query, user_id="ada", limit=5, evidence=False)
    return store.context_from(query, results, user_id="ada", token_budget=budget, entities=[])


def _saved(similar: int):
    store = _store()
    store.config.retrieval.turn_search = False  # the similar turns alone
    store.config.retrieval.similar_turns = similar
    saved = _save_long(store, [_fact("Ada went to Tokyo in March for a wedding", 1)])
    return store, saved


def test_off_by_default_the_context_is_as_before():
    store = _store()
    try:
        assert store.config.retrieval.similar_turns == 0
        store.config.retrieval.turn_search = False
        _save_long(store, [_fact("Ada went to Tokyo in March for a wedding", 1)])
        context = _context(store)
        assert context.similar == [] and context.excerpts == []
        assert "More of what was said" not in context.context.text
    finally:
        store.close()


def test_the_passage_that_matches_is_shown_though_no_memory_rests_on_it():
    store, saved = _saved(10)
    try:
        short, long_, _ = saved.episode_ids
        context = _context(store)
        keys = [t.passage_id for t in context.similar]
        assert f"{long_}:2" in keys  # "Shibuya Crossing is like Tokyo's Times Square"
        assert f"{short}:0" not in keys  # shown already as the memory's evidence
        assert [t.passage_id for t in context.evidence] == [f"{short}:0"]
        assert context.excerpts == context.similar
        episodes = store.backend.episodes_by_id(saved.episode_ids)
        order = [t.passage_id for t in store._passage_turns(
            [episodes[e] for e in saved.episode_ids])]
        assert keys == [k for k in order if k in keys]  # in the order said
        assert ("More of what was said that matches the question, in the order it was said:"
                in context.context.text)
        assert "(part 3 of 4): Shibuya Crossing" in context.context.text
    finally:
        store.close()


def test_they_never_push_a_memory_out():
    for budget in (4000, 400, 160):
        store, _ = _saved(0)
        try:
            for i in range(12):
                store.add(f"Tokyo note {i}: Shibuya was crowded", user_id="ada", infer=False)
            off = _context(store, budget)
            store.config.retrieval.similar_turns = 10
            on = _context(store, budget)
            assert [r.memory.id for r in on.results] == [r.memory.id for r in off.results]
            assert on.evidence == off.evidence
            assert on.context.token_estimate <= budget
            assert estimate_tokens(on.context.text) >= estimate_tokens(off.context.text)
        finally:
            store.close()


def test_the_limit_and_a_turn_a_memory_says_whole():
    store, saved = _saved(1)
    try:
        assert len(_context(store).similar) == 1
        store.config.retrieval.similar_turns = 10
        said = store.add("Shibuya Crossing is like Tokyo's Times Square, the crowds never stop.",
                         user_id="ada", infer=False)
        shown = {t.episode_id for t in _context(store).similar}
        assert not shown & set(said.episode_ids)  # its memory says it whole
    finally:
        store.close()


def test_the_runner_records_them_apart_from_turn_search():
    store, _ = _saved(10)
    try:
        context = _context(store)
        assert context.similar
        assert xb.searched_turns(context) == []
        assert xb.turn_search_record(context)["turns"] == 0
        record = xb.similar_record(context)
        assert record["turns"] == len(context.similar) and record["budget_tokens"] > 0
        # without turn search's turns, the similar turns stay
        assert xb.context_list(context, excerpts=False) == xb.context_list(context)
    finally:
        store.close()
