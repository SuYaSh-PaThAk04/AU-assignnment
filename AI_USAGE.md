# AI Usage Disclosure

## Tools used
Cursor, with the Grok agent in this session.

## What I used them for
- Reading the assignment README, glossary, schemas, and the synthetic data generator.
- Turning Assignment A into a Postgres ingest and report pipeline under `solution/`.
- Writing the unit tests, the hand-built fixture, and the seed 42 / seed 7 checks.
- Drafting this file and `SUBMISSION.md`.

## What I wrote or decided myself
- Chose Assignment A, then chose Postgres instead of SQLite.
- The decisions in `SUBMISSION.md` are the ones to defend: HMAC for the phone, Fernet for the name, file-level idempotency, linking orphans only when the lead exists, and keeping unknown dispositions out of the metrics.
- I need to be able to walk through `solution/ingest.py` and `solution/reports.py` and change them live. I should re-read both before the interview and replace the time estimate and my name in `SUBMISSION.md`.

## Where the AI was wrong or unhelpful
The first schema setup sent the whole DDL script through one `psycopg` `execute`. That driver runs a single statement per call, so the table creation would have failed on the first connection. It was split into separate statements before the tests ran.

The first `docker compose up` also failed because Docker Desktop was not running. Starting Docker Desktop, then compose, fixed it. Nothing was wrong with the compose file.

## How I verified AI-generated code
- `python -m pytest`: 14 unit tests plus the fixture pipeline.
- Seeds 42 and 7: ingesting `drop_1` then `drop_2` matches ingesting the parent directory once, and ingesting again does not change the five report files.
- Seed 42 quality counts: created leads + merged rows + rejections equals `lead_rows_total`.
- Checked that `customer_name_enc` is Fernet ciphertext and that `leads` does not store a raw `+91` phone.
