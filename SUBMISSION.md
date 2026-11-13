# Submission

## Candidate
- **Name:** fill in before you submit
- **Assignment chosen:** A (Data)
- **Time spent (honest estimate):** one Cursor session. Replace this with the hours you actually spent.

## How to run
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
docker compose up -d

python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data
python -m solution ingest data/drop_1
python -m solution ingest data/drop_2
python -m solution report --out reports/

python -m pytest
```

Postgres is the database in `docker-compose.yml` (`postgresql://au:au_local_only@localhost:5432/au_assignment`). `--database-url` or `DATABASE_URL` overrides it. Tables are created in the `ledger` schema (`--schema` or `LEDGER_SCHEMA`). `python -m solution ingest data` reads every `drop_*` child in name order.

Do not commit `data/`, `reports/`, or a database dump.

## Design

Lead CSVs and call-event JSONL land in Postgres. The ledger is append-only. Reports are computed in Python from that database, then written as the five contract files.

```mermaid
flowchart LR
  csv[Lead CSVs] --> leadRows[lead_rows]
  leadRows --> leads[leads]
  jsonl[Event JSONL] --> lines[event_lines]
  lines --> ledger[events]
  leads --> relink[relink]
  ledger --> relink
  relink --> reports[five report files]
```

| Table | Why it exists |
|---|---|
| `leads` | One row per `(workflow_id, phone_hmac)`. `batch_id` is fixed on the first valid row. |
| `lead_rows` | One non-blank CSV row. Rejections, merges, and field warnings are counted from here. |
| `events` | Append-only ledger, primary key `event_id`. `lead_id` stays null until the event can be linked. |
| `event_lines` | Every non-empty line, including malformed and duplicate lines, so quality counts survive a second run. |
| `ingest_files` | Absolute path of a file already committed. A later ingest skips that file. |

PII is not stored in plaintext. The canonical phone (`+91XXXXXXXXXX`) is HMAC-SHA256'd for matching, and only the last 4 digits are kept so the report can mask it as `+91******1234`. `customer_name` is Fernet ciphertext. The key comes from `PII_KEY`. When that variable is unset, a local development key is used. That default is only for this synthetic assignment.

A call exists only when a linked `call.initiated` is present. Its date is the IST date of that timestamp. The effective disposition is the latest `disposition.override` by `occurred_at`, and a `disposition.set` is used only when there is no override. An override still wins when the set happened later. The original events are never updated in place.

`report` runs the same relink pass as `ingest`. A crash after the files are committed, but before relink, is repaired by running either command again.

## Decisions & assumptions

| Decision | Why | Alternative considered |
|---|---|---|
| Postgres, schema `ledger`, via the repo's `docker-compose.yml` | The assignment allows either database. Postgres is what we will run. | SQLite in a local file. |
| Reports computed in Python | The contract is a file diff, and the bucket rules are easier to test as functions. | Aggregation only in SQL. |
| `ingest_files` skips a committed file | Re-running a drop must not change counts. Drop 2 never re-reads drop 1. | Re-parse every file and rely only on primary keys. |
| Orphans are linked at the end, and unknown codes are flagged only after a link | An event can arrive before its lead. Until it links, the spec counts it as an orphan, not as an unknown disposition. | Quarantine table separate from the ledger. |
| Unparseable `occurred_at` is counted as malformed | The ledger stores `timestamptz`. A row with no clock cannot be ordered. The generator only emits the three documented forms. | Store the row with a null timestamp. |
| Tie-break latest disposition and latest call on `event_id` / `call_id` | The spec does not say what to do when two timestamps are equal. Ids from the generator are zero-padded, so the sort is stable. | Leave the tie to whatever the database returns. |
| `batch_summary` omits a batch that owns no leads | A file of only rejections or only duplicates has no `batch_id` on `leads`. | Emit a zero row for every file stem. |
| `rejections_by_reason` lists only reasons with a positive count, keys sorted | The zeros in the README are a shape example. | Emit every possible reason with explicit zeros. |
| Money rounds half-up to 2 places. Connectivity rate rounds half-up to 4 places. | Half-up matches how these figures are usually read. Python's default `round` is half-even. | `format(rate, '.4f')` bankers' rounding. |
| Month names use a fixed English map | `strptime("%b")` follows the Windows locale and can reject `Dec`. | `strptime`. |
| A naive timestamp with a `T` and no offset is also IST | The spec's example uses a space. Accepting `T` does not change the generator's output. | Accept only the space form. |
| Disposition codes are matched exactly, including case | `RENEWD` is not `renewed`. The catalogue is lowercase. | Case-insensitive catalogue lookup. |
| If two CSV columns map to one field, the last non-empty value wins | Alias files should not be wiped by an empty duplicate header. | First column wins. |
| `lead_report` ties on `external_ref` break on the masked phone | The spec's sort key can collide. The extra key only matters for ties. | `lead_id` order. |

## Failure modes considered

- Bad phones, missing required fields, impossible dates, bad amounts, and unknown enums are rejected or nulled with a field warning. Blank rows are not counted.
- Malformed JSON and duplicate `event_id`s are counted and do not enter the ledger twice. Duplicate delivery across drops is one stored event plus one dropped copy.
- Events for a lead that shows up in a later drop are stored immediately and linked when the lead row exists. `disposition.override` has no phone; it links through `call_id` after any other event of that call is linked.
- Unknown disposition codes stay in the ledger and are ignored for buckets, success, and current disposition.
- Re-running `ingest` on a committed file changes nothing. Ingesting `drop_1` then `drop_2` matches ingesting the parent directory once. Checked on seeds 42 and 7.
- A crash mid-file rolls the file's transaction back, because `ingest_files` is written in that same transaction. The next run processes the file again.
- A crash after the file commit and before relink leaves `lead_id` null. The next `ingest` or `report` runs relink.

Not handled:

- Two ingest processes writing the same drop at once.
- Rotating `PII_KEY` after data is loaded. Old HMACs would stop matching.
- `--scale large` under two minutes. Small seed 42 is about 30 seconds. Row-by-row inserts will not be fast enough at 140k leads without batching.
- Scheduling, backfills, and monitoring for 100k leads/day.

## Testing

- Unit tests, no database: phone shapes, day-first dates, money, enums, IST timestamps, header aliases, first-failure rejection, override-vs-set, buckets.
- A hand-built drop checks every bucket, a merge that keeps the original `batch_id`, an override with no phone, a duplicate event, a malformed line, an unknown code, and an event that is an orphan until drop 2.
- Seeds 42 and 7: drop-by-drop matches one ingest of the parent directory, and a second ingest does not change the five files. Names in the database start with the Fernet prefix, and phone numbers are not stored on `leads`.

Not tested: `--scale large`, a real `kill -9` during ingest, or a second Postgres version.

## Results

Seed 42, `--scale small`, on this machine:

- `drop_1` ingest 15.5s, `drop_2` ingest 14.1s, report 0.2s.
- 1,892 leads (951 `motor_renewal`, 941 `policy_feedback`) from 2,057 non-blank rows.
- 108 rejections, 57 merged duplicates, 122 field warnings.
- 8,745 event lines, 8 malformed, 237 duplicate events dropped, 462 still orphaned after both drops, 18 unknown disposition codes.

The orphan count stays high because the generator emits calls for rejected rows and for numbers that were never uploaded. Those must not become leads. Created leads plus merged rows plus rejections equals `lead_rows_total` (1,892 + 57 + 108 = 2,057).

## If I had another day

1. Batch the inserts so `--scale large` finishes in under two minutes, and record that timing.
2. Move daily and batch aggregation into SQL and diff it against the Python reports.
3. Add a backfill command that can rebuild `lead_id` links without re-reading files, plus a note on running this nightly for 100k leads: one transaction per file, retry the file on failure, and never update a ledger row in place.
