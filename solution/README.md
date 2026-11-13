# Solution: Interaction Ledger

Assignment A pipeline. Leads and call events are ingested into Postgres. Reports are derived from that ledger.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
docker compose up -d
```

On macOS or Linux, activate the venv with `source .venv/bin/activate`.

Postgres is the database from `docker-compose.yml` (`au` / `au_local_only` / `au_assignment` on port 5432). Override the URL with `DATABASE_URL` or `--database-url`. Tables are created in the `ledger` schema (`--schema` or `LEDGER_SCHEMA` to use another one).

`PII_KEY` optional. When it is unset, a local development key encrypts names and HMACs phones. Do not use that default for real customer data.

## Run

```bash
python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data
python -m solution ingest data/drop_1
python -m solution ingest data/drop_2
python -m solution report --out reports/
```

`python -m solution ingest data` processes every `drop_*` directory in name order. Ingesting a drop that was already loaded changes nothing. `report` also links events whose lead arrived after the event was stored, so a report after a crash still sees the current ledger.

## Test

```bash
pytest
```
