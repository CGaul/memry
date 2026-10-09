"""Two languages in ``evals/relative_retrieval_benchmark.py``: the writer
told the store's languages, a second file's questions in one language added
to each memory's, the benchmark's questions translated by the text model
(names kept) and scored, and a dry run that counts the calls and makes none.
Offline: scripted models, hash vectors, a small world."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evals import relative_retrieval_benchmark as bench  # noqa: E402
from memry.intelligence.questions import detect_language  # noqa: E402
from memry.providers.llm import LLM  # noqa: E402

EN_DE = "The conversations of this store are in English and German."


class _Script(LLM):
    """A text model that answers each call with ``answer(system, user)`` and
    keeps what it was asked."""

    name = "script"
    available = True

    def __init__(self, answer) -> None:
        self.answer, self.asked = answer, []

    def complete(self, system, user, *, json_schema=None):
        self.asked.append((system, user))
        return self.answer(system, user)


def _numbered(user: str) -> list[tuple[int, str]]:
    return [(int(n), text) for n, text in re.findall(r"^(\d+)\. (.*)$", user, re.M)]


def _german(question: str) -> str:
    """A stand-in translation: the names kept, the words marked German."""
    return "Was ist mit " + question.rstrip("?") + "?"


def _translator(system, user):
    return json.dumps({"items": [{"n": n, "text": _german(q)} for n, q in _numbered(user)]})


def _bilingual_writer(system, user):
    return json.dumps({"items": [
        {"n": n, "questions": [f"What about {t.rstrip('.')}?",
                               f"Was ist mit der Notiz {len(t)} und wo steht sie?"]}
        for n, t in _numbered(user)]})


# ------------------------------------------------------------ the writer

def test_the_writer_is_told_the_languages_and_keeps_both():
    assert bench.questions_system() is bench.QUESTIONS_SYSTEM
    assert bench.questions_system([]) is bench.QUESTIONS_SYSTEM
    system = bench.questions_system(["en", "de"])
    assert EN_DE in " ".join(system.split())
    assert system.index(EN_DE[:20]) < system.index("Answer with JSON only")
    llm = _Script(_bilingual_writer)
    got = bench.write_world_questions(llm, ["Ada lives in Graz.", "Bo likes tea."], {},
                                      lambda q: None, batch=5, workers=1,
                                      languages=["en", "de"])
    assert EN_DE in " ".join(llm.asked[0][0].split())
    assert [detect_language(q) for q in got["0"]] == ["en", "de"]
    # without languages the prompt is the one the English file was written with
    plain = _Script(_bilingual_writer)
    bench.write_world_questions(plain, ["Ada lives in Graz."], {}, lambda q: None,
                                batch=5, workers=1)
    assert plain.asked[0][0] == bench.QUESTIONS_SYSTEM


def test_add_language_questions_adds_the_first_question_in_that_language():
    base = {"0": ["Where does Ada live?", "Which city is Ada in?"], "1": ["What does Bo drink?"],
            "2": ["When did Kai start?"]}
    extra = {"0": ["Where is Ada?", "Wo wohnt Ada?", "In welcher Stadt ist Ada?"],
             "1": ["What is Bo's drink?"], "3": ["Was macht Lena?"]}
    merged, added = bench.add_language_questions(base, extra, "de")
    assert merged == {"0": ["Where does Ada live?", "Which city is Ada in?", "Wo wohnt Ada?"],
                      "1": ["What does Bo drink?"], "2": ["When did Kai start?"],
                      "3": ["Was macht Lena?"]}
    assert added == 2


# ------------------------------------------------------------ the translation

def test_translate_questions_reads_the_numbered_answer():
    llm = _Script(lambda s, u: 'ok {"items": [{"n": 2, "text": " Wo  wohnt\\nAda? "}, '
                               '{"n": 7, "text": "x"}, {"n": 1, "text": 3}]}')
    got = bench.translate_questions(llm, ["What does Bo like?", "Where does Ada live?"], "de")
    assert got == [None, "Wo wohnt Ada?"]
    system, user = llm.asked[0]
    assert "into German" in system and "Keep every name exactly as written" in system
    assert user == "1. What does Bo like?\n2. Where does Ada live?"
    assert bench.translate_questions(_Script(lambda s, u: "no"), ["a"], "de") == [None]


def test_a_translation_that_changed_a_name_is_asked_again_once():
    answers = iter([
        json.dumps({"items": [{"n": 1, "text": "Was verkauft die Brakadu-Bäckerei?"},
                              {"n": 2, "text": "Wo wohnt Ada Berg?"}]}),
        json.dumps({"items": [{"n": 1, "text": "Was verkauft Brakadu Bakery?"}]}),
    ])
    llm = _Script(lambda s, u: next(answers))
    saved = []
    got = bench.translate_world_questions(
        llm, ["What does Brakadu Bakery sell?", "Where does Ada Berg live?"], {},
        ["Brakadu Bakery", "Ada Berg"], "de", lambda t: saved.append(dict(t)),
        batch=5, workers=1)
    assert got == {"What does Brakadu Bakery sell?": "Was verkauft Brakadu Bakery?",
                   "Where does Ada Berg live?": "Wo wohnt Ada Berg?"}
    assert llm.asked[1][1] == "1. What does Brakadu Bakery sell?"
    assert saved[-1] == got


def test_translated_queries_keep_the_answers_and_report_the_missing():
    queries = {"single_fact": [("Where does Ada live?", [3], []), ("What?", [4], [5])]}
    got, missing = bench.translated_queries(queries, {"Where does Ada live?": "Wo wohnt Ada?"})
    assert got == {"single_fact": [("Wo wohnt Ada?", [3], []), ("What?", [4], [5])]}
    assert missing == ["What?"]


def test_estimate_counts_calls_of_25_and_prices_them():
    plan = bench.estimate("x" * 400, ["y" * 40] * 51, lambda _: 60)
    assert plan["calls"] == 3
    assert plan["output_tokens"] == 51 * 60
    assert plan["input_tokens"] == 3 * (100 + 8) + 51 * 11
    assert plan["dollars"]["gpt-6-luna"] == round(
        (plan["input_tokens"] * 0.10 + plan["output_tokens"] * 0.50) / 1e6, 4)


# ------------------------------------------------------------ the command

def _main(monkeypatch, tmp_path, *argv, llm=None):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setattr(bench, "build_llm", lambda cfg: llm or pytest.fail("a model was built"))
    monkeypatch.setattr(sys, "argv", ["bench", "--world", "dense", "--sizes", "250", *argv])
    bench.main()


def test_a_dry_run_counts_the_calls_and_makes_none(monkeypatch, tmp_path, capsys):
    target = tmp_path / "de.json"
    _main(monkeypatch, tmp_path, "--translate-questions", str(target), "--dry-run")
    _main(monkeypatch, tmp_path, "--write-questions", str(tmp_path / "q.json"),
          "--question-languages", "en", "de", "--write-only", "--dry-run")
    out = capsys.readouterr().out
    assert "dry run, translation:" in out and "dry run, questions for" in out
    assert not target.exists() and not (tmp_path / "q.json").exists()


def test_translate_then_score_with_the_translations(monkeypatch, tmp_path, capsys):
    target = tmp_path / "de.json"
    llm = _Script(_translator)
    _main(monkeypatch, tmp_path, "--translate-questions", str(target), llm=llm)
    translations = json.loads(target.read_text())
    world = bench.build_world_dense(250)
    asked = {q for items in world["queries"].values() for q, _, _ in items}
    assert set(translations) == asked
    assert all(v == _german(k) for k, v in translations.items())
    assert len(llm.asked) == -(-len(asked) // bench.QUESTIONS_BATCH)
    capsys.readouterr()
    out = tmp_path / "res.json"
    _main(monkeypatch, tmp_path, "--questions-translated", str(target), "--modes", "hybrid",
          "--links", "none", "--families", "single_fact", "--out", str(out))
    res = json.loads(out.read_text())
    assert res["250|none|hybrid"]["single_fact"]["n"] == len(world["queries"]["single_fact"])


def test_write_only_writes_the_bilingual_file_and_stops(monkeypatch, tmp_path):
    target = tmp_path / "q.json"
    llm = _Script(_bilingual_writer)
    _main(monkeypatch, tmp_path, "--write-questions", str(target), "--question-languages",
          "en", "de", "--write-only", "--out", str(tmp_path / "res.json"), llm=llm)
    written = json.loads(target.read_text())["250"]
    assert len(written) == len(bench.build_world_dense(250)["memories"])
    assert all(EN_DE in " ".join(system.split()) for system, _ in llm.asked)
    assert not (tmp_path / "res.json").exists()
    # and added to a base file, one German question a memory
    base = tmp_path / "base.json"
    base.write_text(json.dumps({"250": {k: [v[0]] for k, v in written.items()}}))
    merged, added = bench.add_language_questions(
        json.loads(base.read_text())["250"], written, "de")
    assert added == len(written)
    assert all([detect_language(q) for q in v] == ["en", "de"] for v in merged.values())


def test_cache_only_stops_on_a_text_not_in_the_cache(tmp_path):
    embedder = bench.CachedEmbedder(bench.CacheOnly(), tmp_path / "none.json")
    embedder.cache["known"] = [0.0] * 3
    assert embedder.embed(["known"]) == [[0.0] * 3]
    with pytest.raises(RuntimeError, match="not in the embedding cache"):
        embedder.embed(["known", "new"])


def test_flags_that_need_another_are_refused(monkeypatch, tmp_path):
    for argv in (["--question-languages", "de"], ["--add-questions", "x.json"],
                 ["--write-only"], ["--dry-run"]):
        with pytest.raises(SystemExit):
            _main(monkeypatch, tmp_path, *argv)


def test_the_cache_writes_a_single_new_text_at_most_once_a_minute(tmp_path):
    class Counting(bench.CacheOnly):
        def embed(self, texts):
            return [[1.0, 0.0, 0.0] for _ in texts]

    path = tmp_path / "cache.json"
    embedder = bench.CachedEmbedder(Counting(), path)
    embedder.embed(["a", "b"])  # a batch is written at once
    assert set(json.loads(path.read_text())) == {"a", "b"}
    embedder.embed(["c"])  # one text, as a search embeds its question: kept for later
    assert set(json.loads(path.read_text())) == {"a", "b"}
    embedder.flush()
    assert set(json.loads(path.read_text())) == {"a", "b", "c"}
    assert embedder.unsaved == 0
