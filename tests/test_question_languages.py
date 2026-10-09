"""Questions in the store's languages: with ``extraction.question_languages``
the question writer (the extractor at save, and the backfill) is told the
languages of the store's turns and writes at least one question in each.
"auto" reads them from the namespace's saved turns. Without it the prompts
are those from before, byte for byte."""

from __future__ import annotations

import json

import pytest
from conftest import FakeLLM, fact, facts_response

from memry.config import Config
from memry.intelligence.extraction import EXTRACTION_SYSTEM, extraction_system
from memry.intelligence.questions import (
    BACKFILL_SYSTEM,
    QUESTIONS_RULE,
    backfill_system,
    detect_language,
    detect_languages,
    language_instruction,
    language_names,
    questions_rule,
    write_questions,
)
from memry.models import Episode, Memory
from memry.providers.embeddings import HashEmbedder
from memry.store import MemoryStore

NO_GAPS = json.dumps({"missing": []})
EN_DE = "The conversations of this store are in English and German."


def _store(languages: list[str], llm: FakeLLM) -> MemoryStore:
    cfg = Config(db_path=":memory:")
    cfg.retrieval.question_keys = True
    cfg.extraction.question_languages = languages
    return MemoryStore(cfg, llm=llm, embedder=HashEmbedder(64))


def _texts(store, memory_id):
    return [q["text"] for q in store.backend.questions_of([memory_id]).get(memory_id, [])]


def _flat(text: str) -> str:
    return " ".join(text.split())


# ------------------------------------------------------------ the instruction

def test_without_languages_the_prompts_are_unchanged():
    assert language_instruction([]) == ""
    assert language_instruction(None) == ""
    assert questions_rule() is QUESTIONS_RULE
    assert questions_rule([]) is QUESTIONS_RULE
    assert backfill_system() == BACKFILL_SYSTEM
    assert extraction_system(ask_questions=True, question_languages=[]) \
        == extraction_system(ask_questions=True)
    # the languages are part of the questions rule only
    assert extraction_system(question_languages=["en", "de"]) == EXTRACTION_SYSTEM


def test_two_languages_ask_for_one_question_in_each():
    text = _flat(language_instruction(["en", "de"]))
    assert text.startswith(EN_DE)
    assert "Write at least one question in each of these languages." in text
    assert "Keep the names of people, things and places as written" in text
    rule = questions_rule(["en", "de"])
    assert rule.startswith(QUESTIONS_RULE)
    assert EN_DE in _flat(rule)
    system = extraction_system(ask_questions=True, question_languages=["en", "de"])
    assert EN_DE in _flat(system)
    assert _flat(system).index(EN_DE) < _flat(system).index("- sources: the numbers")
    # the extraction prompt is formatted with the date after the rule goes in
    assert EN_DE in _flat(system.format(today="2026-10-09"))
    backfill = backfill_system(["en", "de"])
    assert EN_DE in _flat(backfill)
    assert _flat(backfill).index(EN_DE) < _flat(backfill).index("Answer as JSON:")


def test_one_language_asks_for_every_question_in_it():
    assert _flat(language_instruction(["de"])).startswith("Write every question in German.")


def test_language_names_are_named_once_and_at_most_three():
    assert language_names(["en", "DE", "German", " de ", "fr", "es"]) == [
        "English", "German", "French"]
    assert language_names(["Dutch", "{today}"]) == ["Dutch", "today"]
    assert "{" not in extraction_system(ask_questions=True, question_languages=["{x}"]) \
        .replace("{{", "").replace("}}", "").replace("{today}", "")


# ------------------------------------------------------------ the detector

@pytest.mark.parametrize("text, code", [
    ("Where does Ada live?", "en"),
    ("Ada moved to Amsterdam and she works as a nurse.", "en"),
    ("Was verkauft Brakadu Bakery?", "de"),
    ("Wo wohnt Ada? Sie ist nach Amsterdam gezogen.", "de"),
    ("Welche Plattformen unterstützt Brakadu planner v2?", "de"),
    ("Où habite Ada ? Elle est à Amsterdam avec sa sœur.", "fr"),
    ("¿Dónde vive Ada? Ella está en Amsterdam con su hermana.", "es"),
    ("Where was Brakadu planner v1 announced?", "en"),
    ("In welchem Jahr wurde Brakadu Bakery gegründet?", "de"),
    ("An welchem Wochentag trifft sich das Team von Gilduis ledger?", "de"),
    ("Brakadu planner v3", None),
    ("", None),
])
def test_detect_language_by_frequent_short_words(text, code):
    assert detect_language(text) == code


def test_detect_languages_keeps_those_of_a_fifth_of_the_turns_most_frequent_first():
    turns = (["Ich bin heute nach Berlin gefahren und habe Ada getroffen."] * 6
             + ["I met Ada in Berlin and we had lunch."] * 3
             + ["Je suis à Paris avec Ada."] + ["ok", "12:30"])
    assert detect_languages(turns) == ["de", "en"]
    assert detect_languages(turns, min_share=0.05) == ["de", "en", "fr"]
    assert detect_languages(["ok", "👍"]) == []


# ------------------------------------------------------------ the save

def test_a_save_asks_in_the_configured_languages_and_keeps_both():
    llm = FakeLLM()
    store = _store(["en", "de"], llm)
    llm.queue(facts_response({**fact("Ada moved to Amsterdam in March 2024"), "questions": [
        "Which city does Ada live in?", "In welcher Stadt wohnt Ada?",
        "When did Ada move?"]}), NO_GAPS)
    actions = store.add("I moved to Amsterdam in March 2024", user_id="ada").actions
    assert EN_DE in _flat(llm.calls[0][0])
    kept = _texts(store, actions[0].memory_id)
    assert kept == ["Which city does Ada live in?", "In welcher Stadt wohnt Ada?",
                    "When did Ada move?"]
    assert {detect_language(q) for q in kept} == {"en", "de"}
    store.close()


def test_without_languages_the_save_prompt_is_the_one_from_before():
    llm = FakeLLM()
    store = _store([], llm)
    llm.queue(facts_response({**fact("Ada drinks tea"), "questions": ["What does Ada drink?"]}),
              NO_GAPS)
    store.add("I drink tea", user_id="ada")
    assert "The conversations of this store are in" not in llm.calls[0][0]
    assert QUESTIONS_RULE in llm.calls[0][0]
    store.close()


def test_auto_reads_the_languages_of_the_namespaces_saved_turns():
    llm = FakeLLM()
    store = _store(["auto"], llm)
    store.backend.add_episodes([
        Episode(content=text, user_id="ada") for text in [
            "Ich wohne seit März in Amsterdam und arbeite als Pflegerin.",
            "Wir haben die Wohnung über einen Freund gefunden.",
            "Mein Bruder wohnt auch in der Stadt.",
        ]])
    # another namespace's turns are not read
    store.backend.add_episodes([Episode(content="Je suis à Paris avec ma sœur.", user_id="bo")])
    llm.queue(facts_response({**fact("Ada works the night shift"), "questions": [
        "When does Ada work?", "Wann arbeitet Ada?"]}), NO_GAPS)
    actions = store.add("I work the night shift now", user_id="ada").actions
    system = _flat(llm.calls[0][0])
    assert "The conversations of this store are in German and English." in system
    assert "French" not in system
    assert {detect_language(q) for q in _texts(store, actions[0].memory_id)} == {"en", "de"}
    assert store.question_languages("ada") == ["de", "en"]
    store.close()


def test_auto_beside_a_configured_language_keeps_it_first():
    store = _store(["es", "auto"], FakeLLM())
    store.backend.add_episodes([Episode(content="I live in Amsterdam with my brother.",
                                        user_id="ada")])
    assert store.question_languages("ada") == ["es", "en"]
    store.close()


def test_the_languages_are_not_read_with_question_keys_off():
    llm = FakeLLM()
    store = _store(["auto"], llm)
    store.config.retrieval.question_keys = False
    read = []
    store.question_languages = lambda *a, **k: read.append(a) or []
    llm.queue(facts_response(fact("Ada drinks tea")), NO_GAPS)
    store.add("I drink tea", user_id="ada")
    assert read == []
    store.close()


# ------------------------------------------------------------ the backfill

def test_write_questions_passes_the_languages_to_the_prompt():
    llm = FakeLLM([json.dumps({"items": [{"n": 1, "questions": ["Where does Ada live?"]}]})])
    write_questions(llm, ["Ada lives in Amsterdam"], ["en", "de"])
    assert EN_DE in _flat(llm.calls[0][0])


def test_the_backfill_asks_in_the_store_languages_and_keeps_both():
    llm = FakeLLM()
    store = _store(["en", "de"], llm)
    memory = store.backend.insert_memory(Memory(content="Ada moved to Amsterdam", user_id="ada"))
    llm.queue(json.dumps({"items": [{"n": 1, "questions": [
        "Where does Ada live?", "Wo wohnt Ada?", "Wohin ist Ada gezogen?"]}]}))
    assert store.backfill_questions(user_id="ada") == {"checked": 1, "written": 1}
    assert EN_DE in _flat(llm.calls[0][0])
    kept = _texts(store, memory.id)
    assert kept == ["Where does Ada live?", "Wo wohnt Ada?", "Wohin ist Ada gezogen?"]
    assert [detect_language(q) for q in kept] == ["en", "de", "de"]
    store.close()


# ------------------------------------------------------------ the setting

def test_the_setting_reads_from_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMRY_QUESTION_LANGUAGES", "en, de")
    cfg = Config.load(tmp_path / "none.json")
    assert cfg.extraction.question_languages == ["en", "de"]
    monkeypatch.delenv("MEMRY_QUESTION_LANGUAGES")
    assert Config.load(tmp_path / "none.json").extraction.question_languages == []
