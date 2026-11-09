# job-hunt-agent

Finds data science and AI openings for September 2027 starts (graduate schemes,
internships, full-time roles), mostly in London and at employers that can
sponsor a visa, and keeps track of them.

Jobs come from:

- big job boards: Indeed and LinkedIn (through JobSpy), Google Jobs (through
  SerpApi, needs a free key), Glassdoor when it isn't blocked
- companies' own job boards, read directly: Greenhouse, Lever (global and EU),
  Ashby, Workable, SmartRecruiters. The list starts from
  `config/seed_companies.yaml` (121 London employers with a sponsor licence)
  and grows whenever a job-board advert links to a company's board.
- coming later: UK boards (Adzuna, Reed), graduate trackers, search-engine
  discovery, student portals that need a login, the UK sponsor register

## Setup

Python 3.10+.

```bash
source ~/Documents/venv-3.10/bin/activate
pip install -e ".[dev]"            # add ,postgres to use Postgres instead of SQLite
cp config/.secrets.example.yaml config/.secrets.yaml   # only once a source needs a key
job-hunt migrate-db
pytest
ruff check src tests migrations && ruff format --check src tests migrations
```

## Usage

```bash
job-hunt migrate-db                       # bring the database schema up to date (safe to rerun)
job-hunt seed-companies                   # load config/seed_companies.yaml (safe to rerun)
job-hunt add-company --name Perplexity --board-url https://jobs.ashbyhq.com/perplexity
job-hunt add-company --name Anthropic --ats greenhouse --slug anthropic
job-hunt list-companies                   # boards read on every fetch
job-hunt list-sources                     # every source, enabled or not
job-hunt fetch                            # every enabled source
job-hunt fetch --source indeed --source ashby   # just these (even if disabled)
job-hunt job-counts                       # jobs found so far, per source
job-hunt show-settings                    # the settings this run would use
job-hunt clear-cache                      # drop every cached response
```

`python -m job_hunt <command>` does the same without installing. `fetch` exits
with 1 if any source failed, so a scheduled run gets noticed.

What gets searched (role terms, locations, how far back) and which sources run
are in `config/settings.yaml` under `job_search` and `job_sources`.

## Configuration

Settings are loaded by Dynaconf and checked by Pydantic
(`src/job_hunt/common/settings.py`). Later files win:

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

Everything lives in one package, `src/job_hunt/`:

```
__main__.py              the one entrypoint (`job-hunt` / `python -m job_hunt`)
cli/                     one class per command, registered by decorator
common/                  settings, logger, registry, HTTP client
common/database/         DatabaseFactory + backends, Database (transactions), migrations runner
common/cache/            CacheFactory + backends, Cache (prefix, TTL, get_or_compute)
job_store/               tables and the repositories that read and write them
job_sources/             JobSource base, JobSourceFactory, location matching
job_sources/job_boards/  Indeed, LinkedIn, Glassdoor, Google Jobs (JobSpy)
job_sources/ats_boards/  Greenhouse, Lever, Ashby, Workable, SmartRecruiters; board link finder
job_pipeline/            JobFetchRun: runs sources, saves jobs, adds linked companies
migrations/              (repo root) Alembic environment and migration scripts
```

The database, cache and logs go in `data/` and `logs/`, both gitignored.

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

Everything with more than one implementation sits behind a factory and a
registry, so adding one is a single class plus its settings block:

- **Job source**: subclass `JobSource` (or `JobSpySource` / `AtsBoardSource`),
  decorate with `@register_job_source`, add a block under `job_sources` in
  settings.yaml and its model in `settings_schema.py`. A test checks every
  source has settings and every settings block has a source.
- **ATS**: subclass `AtsBoardSource`, set `ats_platform` (add it to
  `AtsPlatform`; no migration needed), and implement `board_slug_from_url`, so
  links to the new ATS are recognised too.
- **Database or cache backend**: subclass `DatabaseBackend` / `CacheBackend` in
  its `backends.py` and register it.
- **Command**: subclass `CliCommand` and decorate with `@register_cli_command`.
