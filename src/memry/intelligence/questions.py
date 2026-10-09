"""Question keys: the questions a memory answers, written when the memory is
and kept beside it as extra search keys (``retrieval.question_keys``).

A memory is a fact in the third person ("Ada moved to Amsterdam in March
2024"); a question asked later shares few words with it ("what city is Ada
based in?"). On LoCoMo, three questions written per record and indexed
beside its text raised the share of questions whose answer reaches the top
8 by 11 to 15 points for every writing model, and by 7 to 17 as extra
vectors next to the record's own (PhD notes, dreaming-questions, E196 to
E198). Appended, never in place of the text: questions alone pay only with
the strongest writer.

Here: the rule the extractor is given, the shape of the answer, and the
cleaning every source of questions goes through (extraction, the backfill,
an agent's own). The store writes them (``MemoryStore._write_questions``),
the backend keeps them (``memory_questions``) and the search reads them as
two more candidate lists (``retrieval.hybrid_search``).
"""

from __future__ import annotations

import re
import textwrap
from typing import Any

#: Metadata mark on a memory the backfill asked about and got no question
#: for, so it is not asked again (``MemoryStore.backfill_questions``).
QUESTIONS_CHECKED_KEY = "questions_checked"

#: Questions kept per memory from one source, and in all.
QUESTIONS_PER_MEMORY = 3
QUESTIONS_LIMIT = 9

#: What the extractor is asked, placed among its rules. One question in
#: other words than the fact is where the gain is: the questions sharing
#: few words with the record gained most (C3 in the PhD notes).
QUESTIONS_RULE = """- questions: 2 or 3 questions this fact answers, as the person would ask them
  later in another conversation. Each stands on its own: it names the thing
  or person it asks about (never "it", "he" or "they" alone) and asks for one
  thing the fact says. At least one uses other words than the fact does. []
  when the fact answers no question anyone would ask.
"""

#: Names of the languages the writer can be told about. Any other entry of
#: ``extraction.question_languages`` is passed on as written ("Dutch").
LANGUAGE_NAMES = {"en": "English", "de": "German", "fr": "French", "es": "Spanish"}

#: At most this many languages are asked for: a memory keeps 3 questions
#: from one source (``QUESTIONS_PER_MEMORY``).
LANGUAGES_LIMIT = 3


def language_names(languages: list[str] | tuple[str, ...] | None) -> list[str]:
    """``languages`` as the writer is told them: a known code as its name,
    anything else as written, each once, at most ``LANGUAGES_LIMIT``."""
    names: list[str] = []
    for item in languages or []:
        # no braces: the extraction prompt is formatted after this goes in
        text = " ".join(str(item).replace("{", " ").replace("}", " ").split())
        name = LANGUAGE_NAMES.get(text.casefold(), text)
        if name and name.casefold() not in {n.casefold() for n in names}:
            names.append(name)
        if len(names) == LANGUAGES_LIMIT:
            break
    return names


def language_instruction(languages: list[str] | tuple[str, ...] | None) -> str:
    """The sentence the writer is given about the store's languages, "" for
    none (the writer chooses). With one language, every question is in it;
    with more, at least one question is in each. Names stay as written in
    every language, since the search matches them by their words."""
    names = language_names(languages)
    if not names:
        return ""
    if len(names) == 1:
        return (f"Write every question in {names[0]}. Keep the names of people, "
                "things and places as written.")
    listed = ", ".join(names[:-1]) + " and " + names[-1]
    return (f"The conversations of this store are in {listed}. Write at least one "
            "question in each of these languages. Keep the names of people, things "
            "and places as written in every language.")


def questions_rule(languages: list[str] | tuple[str, ...] | None = None) -> str:
    """``QUESTIONS_RULE``, with the language instruction as its last line when
    ``languages`` are given. Without them it is ``QUESTIONS_RULE`` byte for
    byte, so the provider's prompt cache holds."""
    extra = language_instruction(languages)
    if not extra:
        return QUESTIONS_RULE
    return QUESTIONS_RULE + textwrap.fill(
        extra, width=78, initial_indent="  ", subsequent_indent="  ") + "\n"


#: The answer field, a list of strings.
QUESTIONS_FIELD: dict[str, Any] = {"type": "array", "items": {"type": "string"}}

_MAX_CHARS = 200


def clean_questions(raw: Any, limit: int = QUESTIONS_LIMIT) -> list[str]:
    """The questions of a model's or an agent's answer as the store keeps
    them: strings only, stripped, on one line, at most ``_MAX_CHARS`` each,
    each once (ignoring case), at most ``limit``. Anything else is read as
    none given."""
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        text = " ".join(item.split())
        if not text or len(text) > _MAX_CHARS:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def merge_questions(*lists: list[str], limit: int = QUESTIONS_LIMIT) -> list[str]:
    """The questions of several lists, first list first, each once (ignoring
    case), at most ``limit``: a merged text answers what both its parts
    answered."""
    merged: list[str] = []
    for items in lists:
        merged.extend(items)
    return clean_questions(merged, limit=limit)


#: The backfill's prompt: the same rule as the extractor's, for memories
#: saved before there were question keys, many in one call.
BACKFILL_SYSTEM = """You write the questions that saved memories answer, so that a
question asked later in another conversation finds the memory that answers it.

For each numbered memory, write 2 or 3 questions it answers, as the person
would ask them later. Each question stands on its own: it names the thing or
person it asks about (never "it", "he" or "they" alone) and asks for one thing
the memory says. At least one uses other words than the memory does. Give []
for a memory that answers no question anyone would ask.

Answer as JSON: {"items": [{"n": <the memory's number>, "questions": [str]}]},
one item per memory."""

#: The backfill's answer: the questions of each memory, by its number.
BACKFILL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "questions": QUESTIONS_FIELD,
                },
                "required": ["n", "questions"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["items"],
    "additionalProperties": False,
}


def backfill_system(languages: list[str] | tuple[str, ...] | None = None) -> str:
    """``BACKFILL_SYSTEM``, with the language instruction after the rule when
    ``languages`` are given."""
    extra = language_instruction(languages)
    if not extra:
        return BACKFILL_SYSTEM
    rule_end = "\n\nAnswer as JSON:"
    return BACKFILL_SYSTEM.replace(rule_end, "\n" + textwrap.fill(extra, width=78) + rule_end, 1)


def write_questions(llm: Any, memories: list[str],
                    languages: list[str] | tuple[str, ...] | None = None) -> list[list[str]]:
    """The questions each of ``memories`` answers, asked in one call: one list
    per memory, in order, cleaned as every source is, [] where the model gave
    none or an answer that cannot be read. The memories are shown numbered
    from 1, and an item whose number names no memory is ignored. A failing
    call raises, so the caller can tell a batch it could not ask from one the
    model found nothing in. ``languages`` adds the language instruction
    (``language_instruction``)."""
    out: list[list[str]] = [[] for _ in memories]
    if not memories:
        return out
    # Imported here: extraction.py imports this module at its top, so a
    # top-level import in this direction would be a cycle.
    from .extraction import parse_lenient_json

    listing = "\n".join(
        f"{n}. {' '.join(str(text or '').split())}" for n, text in enumerate(memories, 1))
    raw = llm.complete(
        backfill_system(languages),
        f"Memories:\n{listing}\n\nAnswer for every number as JSON.",
        json_schema=BACKFILL_SCHEMA,
    )
    parsed = parse_lenient_json(raw)
    items = parsed.get("items") if isinstance(parsed, dict) else parsed
    if not isinstance(items, list):
        return out
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            n = int(item.get("n"))
        except (TypeError, ValueError):
            continue
        if not 1 <= n <= len(memories):
            continue
        out[n - 1] = clean_questions(item.get("questions"), limit=QUESTIONS_PER_MEMORY)
    return out


# ---------------------------------------------------------------- languages

#: Short words frequent in each language and rare in the others: enough to
#: tell German, English, French and Spanish turns apart without a library.
#: "in" is in no list, since it is as frequent in German as in English. "an"
#: and "es" are German only. "was" counts as German as a text's first word
#: ("Was verkauft Brakadu Bakery?") and as English anywhere else ("Where was
#: it announced?").
_STOPWORDS: dict[str, frozenset[str]] = {
    "en": frozenset("""the and is are was were of to that it with for on this you
        have has what where who which when how does did do not but be at from they
        we my your his her their or will would can there about""".split()),
    "de": frozenset("""der die das und ist sind war waren nicht ein eine einen einem
        dem den des mit für auf ich du er sie wir ihr wo wer wann wie wem wen wessen
        welche welcher welches welchem welchen woran wofür womit worauf hat haben
        hatte wird werden wurde wurden zu zum zur vom im am an auch noch bei aus
        nach über mein dein sein kein oder aber dass gibt es sich""".split()),
    "fr": frozenset("""le la les et est sont était un une des du de que qui dans pour
        pas sur avec ce cette il elle nous vous ils je mon ma mes où quel quelle
        quand comment aussi mais ou au aux""".split()),
    "es": frozenset("""el la los las y es son era un una unos de del que en para por
        con no se su sus lo como pero donde dónde cuál quién cuándo qué está están
        yo tu mi muy también al""".split()),
}
_WORD = re.compile(r"[^\W\d_]+")


def detect_language(text: str) -> str | None:
    """The language of ``text`` among German, English, French and Spanish,
    by the share of its words that are short frequent words of each. None
    when no such word is in it, or when two languages tie."""
    words = [w.casefold() for w in _WORD.findall(text or "")]
    if not words:
        return None
    counts = {code: sum(w in stop for w in words) for code, stop in _STOPWORDS.items()}
    if words[0] == "was":  # the German question word, not English "was"
        counts["en"] -= 1
        counts["de"] += 1
    best = max(counts.values())
    if best == 0:
        return None
    leaders = [code for code, n in counts.items() if n == best]
    return leaders[0] if len(leaders) == 1 else None


def detect_languages(texts: list[str], *, min_share: float = 0.2,
                     limit: int = LANGUAGES_LIMIT) -> list[str]:
    """The languages of ``texts`` (saved turns), most frequent first: each
    language of at least ``min_share`` of the texts whose language is read
    (``detect_language``), at most ``limit``. [] when no text is read."""
    counts: dict[str, int] = {}
    for text in texts:
        code = detect_language(text)
        if code:
            counts[code] = counts.get(code, 0) + 1
    total = sum(counts.values())
    if not total:
        return []
    ranked = sorted(counts, key=lambda code: (-counts[code], code))
    return [code for code in ranked if counts[code] / total >= min_share][:limit]
