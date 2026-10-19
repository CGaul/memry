# Storage dialects: one SQL implementation, SQLite or PostgreSQL underneath

Status: proposal (fork `CGaul/memry`, branch `design/storage-dialects`). Diagram:
[storage-dialects-c4.mmd](storage-dialects-c4.mmd).

## Why, and the objection this has to answer

`docs/architecture.md` §1 records that PostgreSQL was removed on 2026-07-24
(`d131e14`) because a second SQL implementation meant "two implementations, two
migrations, and two test paths" for every feature, with no deployment that needed
it. Any reintroduction is, in its own words, "a new, reviewed architecture
decision with a migration plan". This design answers that objection directly:

- **One implementation.** The old `postgres.py` was a parallel 584-line backend.
  Here `LocalBackend`'s logic becomes `SqlBackend`, written once against four
  narrow ports; SQLite and PostgreSQL differ only in adapters.
- **One test path.** The existing 1,121 tests run unchanged on both engines (the
  `config` fixture picks the engine from `MEMRY_TEST_DB_URL`), so a feature that
  breaks parity fails CI instead of drifting.
- **SQLite stays the default** and behaves byte-for-byte as today; nothing changes
  for an install that never sets `MEMRY_DB_URL`.

The product case it would serve: several `memry serve` replicas (or one per host)
over one store, and deployments that already run PostgreSQL (backups, PITR,
monitoring) and do not want a second database to operate.

## Ports

| Port | Responsibility | SQLite adapter | PostgreSQL adapter |
|---|---|---|---|
| `Dialect` | placeholders (`?` vs `%s`), upsert, JSON path + array expansion (`json_extract`/`json_each` vs `->>`/`jsonb_array_elements_text`), binary type, schema + migrations | today's DDL + in-code `_ensure_*` upgrades | numbered migrations from the current schema (no legacy upgrade path needed) |
| `KeywordIndex` | BM25 score per memory/episode for a set of words, document frequencies, trigram name lookup | FTS5 `bm25()`, `fts5vocab`, `tokenize='trigram'` — unchanged | `PostingIndex`: term postings written alongside each row; BM25 computed in SQL; `pg_trgm` GIN index for names |
| `VectorIndex` | cosine candidates | numpy scan over BLOB, optional usearch sidecar — unchanged | numpy scan over `bytea` (same code); `pgvector` later, optional |
| `WriteGuard` | serialize read-modify-write sequences; claim enrichment work | `threading.RLock` + `BEGIN` — unchanged | `pg_advisory_xact_lock` (global first, per-namespace later); `FOR UPDATE SKIP LOCKED` lease on pending rows so two workers never enrich the same memory |

`AccountStore` (`accounts.py`, `auth.db`) gets the same treatment: replicas must
share logins too. `memry.snapshot` stays SQLite-only; PostgreSQL deployments back
up with their own tooling, and `memry export` keeps working on both.

## The hard part: BM25 parity

`_keyword_scores` is a MaxScore top-k over FTS5's `bm25()` and `fts5vocab`
counts; retrieval quality is measured against it. PostgreSQL has no BM25
(`ts_rank` is not BM25), and its tokenizers differ from FTS5's `unicode61`.
`PostingIndex` therefore owns both halves:

1. a Python port of `unicode61` (case folding, diacritic removal, Unicode
   separator classes), so both engines see the same terms;
2. `memory_terms(memory_id, term, tf)` + per-row length, maintained in the same
   transaction as the row, and the Okapi formula FTS5 uses (k1 = 1.2, b = 0.75,
   its idf clamp) computed in SQL.

A parity test indexes one corpus in both engines and asserts identical scores
(within float tolerance) and identical rankings; the retrieval benchmarks in
`evals/` must give identical numbers.

## Triggers

The `entity_names` index is maintained by SQLite triggers that use `json_each`.
PostgreSQL gets the same triggers as PL/pgSQL functions, generated from one
definition where possible. Moving the maintenance into application code would
change SQLite's behaviour, so the triggers stay triggers.

## How it would ship upstream (three PRs, each reviewable on its own)

1. **Seam, SQLite only, no behaviour change**: `SqlBackend` + the four ports +
   SQLite adapters; `LocalBackend` stays as an alias. Proof: the existing suite
   passes unchanged.
2. **`PostingIndex` + parity tests**, still exercised against SQLite (the posting
   index can run on SQLite too, which is what makes the parity test cheap).
3. **PostgreSQL adapters**: `memry[postgres]` extra (psycopg 3 + pool),
   `MEMRY_DB_URL`, CI job with a Postgres service running the full suite, a
   decision-record row, docs.

MySQL fits the same ports (`MySqlDialect`), but it lacks partial indexes,
advisory transaction locks of the same shape, and transactional DDL, so it is not
part of this plan.
