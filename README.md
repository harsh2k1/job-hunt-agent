# job-hunt-agent

Finds data science and AI openings for September 2027 starts (graduate schemes,
internships, full-time roles), mostly in London and at employers that can
sponsor a visa, and keeps track of them.

Jobs come from three places:

- job boards and aggregators (Adzuna, Reed, graduate deadline trackers)
- company job boards read directly (Greenhouse, Lever, Ashby, ...), with the
  list of companies growing from search results and the UK sponsor register
- student portals that need a login (Student Circus, Gradcracker, ...)

## Setup

Python 3.10+.

```bash
source ~/Documents/venv-3.10/bin/activate
pip install -e ".[dev]"            # add ,postgres to use Postgres instead of SQLite
cp config/.secrets.example.yaml config/.secrets.yaml   # only once a source needs a key
job-hunt migrate-db
pytest
```

## Usage

```bash
job-hunt migrate-db      # bring the database schema up to date (safe to rerun)
job-hunt show-settings   # print the settings this run would use
job-hunt job-counts      # how many jobs each source has found so far
job-hunt clear-cache     # drop every cached response
```

`python src/main.py <command>` does the same without installing.

## Configuration

Settings are loaded by Dynaconf and checked by Pydantic (`src/common/settings.py`).
Later files win:

| File | What goes in it | Committed |
|---|---|---|
| `config/settings.yaml` | every default | yes |
| `config/settings.local.yaml` | your local overrides, same layout | no |
| `config/.secrets.yaml` | API keys and logins | no |
| `JOBHUNT_*` env vars | one-off overrides, e.g. `JOBHUNT_LOGGING__LEVEL=DEBUG` | - |

A misspelled or unknown key stops the run at startup.

The database and cache each have a `backend` setting. Database: `sqlite`
(default) or `postgres` (put the password in `.secrets.yaml`). Cache: `disk`
(default, kept between runs) or `memory` (one run only).

## Layout

```
src/main.py               the one entrypoint
src/common/settings.py    settings singleton
src/common/logger.py      project logger
src/common/registry.py    name -> class lookup behind every factory
src/common/database/      DatabaseFactory, one backend class per database, Database (transactions)
src/common/cache/         CacheFactory, one backend class per store, Cache (prefix, TTL, get_or_compute)
src/common/http_client.py shared HTTP client: retries, backoff, per-host request gap
src/job_store/            job and company tables and the repositories that read and write them
migrations/               Alembic environment and migration scripts
```

## Database migrations

Tables are only ever created or changed by Alembic migrations in
`migrations/versions/`, on every backend. After changing a model:

```bash
alembic revision --autogenerate -m "add salary to jobs"   # then read the generated file
job-hunt migrate-db
```

`alembic upgrade head --sql` prints the SQL without running it. A test fails if
a model changes without a migration.

## Extending

Adding a database or cache backend: subclass `DatabaseBackend` / `CacheBackend`
in its `backends.py`, register it with the decorator, and add its settings
block. Nothing else changes.

The database, cache and logs go in `data/` and `logs/`, both gitignored.
