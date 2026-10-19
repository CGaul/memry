#!/usr/bin/env python3
"""Extract the human-authored turns from one Claude Code session transcript.

Why this exists: a workflow's decisions and pushbacks live only inside its
session transcripts, and those are enormous — measured on this repository the
largest single transcript is 33 MB across 6 015 lines, of which the
human-authored text is 162 KB (0.49%). The unfiltered form fits in no context
window; the filtered form is a few tens of thousands of tokens and is
*complete* for the purpose, because AWM types its review-gate comments into
the sessions as prompts, so they appear here as ordinary user turns.

The transcript JSONL format is a Claude Code internal with no compatibility
guarantee. Every failure mode therefore degrades to an EMPTY decision log
rather than to a wrong one, and the exit code is always 0: a brief with one
session missing is far better than no brief at all.

Usage:
    extract_decisions.py <transcript.jsonl> [--step <kind>]
                         [--max-chars N] [--max-total N]

Output (stdout, always this shape):
    {"step": "...", "transcript": "...",
     "entries": [{"timestamp": "...", "text": "..."}], "truncated": false}
"""

import argparse
import json
import re
import sys

# Turns that are machinery, not a person talking. `<local-command-caveat>` and
# `<local-command-stdout>` are the CLI echoing its own slash commands;
# `<task-notification>` is a background agent reporting in; `<system-reminder>`
# is injected context. All arrive as type:"user" and none of them is a decision.
NOISE_PREFIXES = (
    "<local-command-caveat>",
    "<local-command-stdout>",
    "<task-notification>",
    "<system-reminder>",
    "Caveat:",
    # A teammate agent reporting in. It wears a user role but no human wrote it,
    # and in a subagent-heavy session it outnumbers the real turns 8 to 4.
    "Another Claude session sent a message:",
)

SYSTEM_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)
COMMAND_NAME = re.compile(r"<command-name>\s*(.*?)\s*</command-name>", re.DOTALL)
COMMAND_ARGS = re.compile(r"<command-args>\s*(.*?)\s*</command-args>", re.DOTALL)


def content_text(content):
    """The human-typed text of a message, or '' when there is none.

    A string is the plain case. A list is either tool output (blocks of type
    tool_result — no text blocks, so this yields '') or a prompt with images
    attached (text + image blocks — the text blocks are the prompt).
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return ""


def normalize(text):
    """Clean one turn down to what a reader needs, or '' to drop it."""
    text = text.strip()
    if not text:
        return ""

    # A slash command IS the step's entry instruction ("/implement 11") and is
    # worth keeping — but only as the command, not as the CLI's XML envelope.
    # The envelope's tags come in either order, so match the family, not one tag.
    if text.startswith("<command-"):
        name = COMMAND_NAME.search(text)
        if not name:
            return ""
        args = COMMAND_ARGS.search(text)
        slug = name.group(1).strip()
        rest = (args.group(1).strip() if args else "")
        if not slug:
            return ""
        if not slug.startswith("/"):
            slug = "/" + slug
        return f"{slug} {rest}".strip()

    if text.startswith(NOISE_PREFIXES):
        return ""

    # A reminder can also ride along *inside* an otherwise real turn.
    text = SYSTEM_REMINDER.sub("", text).strip()
    return text


def is_human_turn(entry):
    if entry.get("type") != "user":
        return False
    # isMeta: context Claude Code injected. toolUseResult: tool output wearing a
    # user role. isSidechain: a subagent's prompt. isCompactSummary: a
    # machine-written recap of the conversation so far.
    return not (
        entry.get("isMeta")
        or entry.get("isSidechain")
        or entry.get("isCompactSummary")
        or "toolUseResult" in entry
    )


def extract(path, max_chars, max_total):
    entries = []
    truncated = False
    total = 0
    try:
        # Streamed line by line: these files reach tens of megabytes and must
        # never be read into memory whole.
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue  # a torn last line while the session is live
                if not isinstance(entry, dict) or not is_human_turn(entry):
                    continue
                message = entry.get("message")
                if not isinstance(message, dict):
                    continue
                text = normalize(content_text(message.get("content")))
                if not text:
                    continue
                if len(text) > max_chars:
                    text = text[:max_chars].rstrip() + " …[truncated]"
                    truncated = True
                if total + len(text) > max_total:
                    truncated = True
                    break
                total += len(text)
                timestamp = entry.get("timestamp")
                entries.append(
                    {"timestamp": timestamp if isinstance(timestamp, str) else None, "text": text}
                )
    except OSError:
        return [], False  # missing/unreadable transcript — an empty log, not an error
    return entries, truncated


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("transcript")
    parser.add_argument("--step", default="", help="pipeline step this session ran (echoed back)")
    parser.add_argument("--max-chars", type=int, default=4000, help="per-turn cap")
    parser.add_argument("--max-total", type=int, default=200_000, help="cap across all turns")
    args = parser.parse_args()

    try:
        entries, truncated = extract(args.transcript, args.max_chars, args.max_total)
    except Exception:
        # An unrecognised shape must yield no entries, never a stack trace: the
        # brief is still produced, just without this session's decisions.
        entries, truncated = [], False

    json.dump(
        {
            "step": args.step,
            "transcript": args.transcript,
            "entries": entries,
            "truncated": truncated,
        },
        sys.stdout,
        indent=2,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
