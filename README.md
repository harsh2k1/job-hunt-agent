# job-hunt-agent

Finds data science and AI openings for September 2027 starts (graduate schemes,
internships, full-time roles) in London and across the UK, at employers that
can sponsor a visa, and keeps track of them.

Jobs come from:

- big job boards: Indeed and LinkedIn (through JobSpy), Google Jobs (through
  SerpApi, needs a free key), Glassdoor when it isn't blocked
- UK boards with their own API: Adzuna and Reed (free keys)
- big employers' own careers searches: Microsoft, Amazon, Meta
- the monthly Hacker News "Who is hiring?" thread, adverts mentioning the UK
- The Intake (graduate schemes, internships, spring weeks), jobs.ac.uk
  (universities and research institutes) and Student Circus (visa-sponsoring
  employers), read from each advert's schema.org JobPosting markup, closing
  dates included
- King's CareerConnect (KCL Target Connect), read in a browser with a saved
  sign-in (a person signs in once with passkey / 2FA)
- job alert emails in Gmail (read only): LinkedIn, Indeed, and the portals
  that can't be read directly (Gradcracker, Handshake, Welcome to the
  Jungle, TargetJobs, Prospects)
- companies' own job boards, read directly: Greenhouse, Lever (global and EU),
  Ashby, Workable, SmartRecruiters, Teamtailor, Recruitee, Pinpoint, Personio,
  BambooHR, Workday. The list starts from `config/seed_companies.yaml` (121 London
  employers with a sponsor licence) and grows whenever an advert from another
  source links to a company's board, and through `discover-companies`, which
  finds boards two ways: web searches limited to ATS sites, and Common
  Crawl's public URL index (every crawled board on each ATS, kept if it has
  UK jobs).
- coming later:
  discovery, student portals that need a login, the UK sponsor register

## Setup

Python 3.10+.

```bash
source ~/Documents/venv-3.10/bin/activate
pip install -e ".[dev]"            # add ,postgres to use Postgres instead of SQLite
cp config/.secrets.example.yaml config/.secrets.yaml   # API keys and the KCL login
job-hunt migrate-db
pytest
ruff check src tests migrations && ruff format --check src tests migrations
```

## Usage

```bash
job-hunt run                              # the daily run: sponsor register, discovery, every source, health
job-hunt run --all-steps                  # every step, even ones not due today (see daily_run in settings)
job-hunt migrate-db                       # bring the database schema up to date (safe to rerun)
job-hunt seed-companies                   # load config/seed_companies.yaml (safe to rerun)
job-hunt add-company --name Perplexity --board-url https://jobs.ashbyhq.com/perplexity
job-hunt add-company --name Anthropic --ats greenhouse --slug anthropic
job-hunt list-companies                   # boards read on every fetch
job-hunt discover-companies               # find new boards: web search + Common Crawl (budgeted, daily)
job-hunt discover-companies --via common-crawl --dry-run   # one way only, show without adding
job-hunt list-sources                     # every source, enabled or not
job-hunt fetch                            # every enabled source
job-hunt fetch --source indeed --source ashby   # just these (even if disabled)
job-hunt job-counts                       # jobs found so far, per source
job-hunt listings                         # live adverts, one per advert across sources
job-hunt source-health                    # each source's last run against its usual count
job-hunt update-sponsor-register          # load the latest Home Office sponsor register (daily is fine)
job-hunt check-sponsor "Monzo"            # is this employer a licensed sponsor?
job-hunt show-settings                    # the settings this run would use
job-hunt clear-cache                      # drop every cached response
```

### Gradcracker (saved pages)

Gradcracker answers automated requests with a bot check, so its result pages
are saved from a browser instead: one folder per day under
`data/gradcracker-jobs/` named for the day (`18-11-2026/`), any `.html` file
names (`Grad-AI-Page1.html`, `Intern-DS-Page1.html`). `job-hunt run` (or
`job-hunt fetch --source gradcracker`) reads every folder; a job counts as
seen on the day of the folder it's in. Saving just page 1, sorted by date
posted, each day is enough: a job stays listed until its deadline, or 60
days after it was last saved if it has none ("Ongoing").

The cards carry no advert text, so each card the sheet would show gets one
web search for its title and employer (source `employer_adverts`, about 40 a
run). A result is used only when the page's own title matches, it names the
same employer and it holds a real advert; only pages a site's robots.txt
allows are read. The employer's copy then joins the card's listing, so its
JD is shown and screened, and a company job board it's on is tracked from
then on. Cards with no copy found keep their card facts and are searched
again after 14 days.

### How often each step runs

`daily_run.every_days` in `config/settings.yaml` sets it, one line per step:
fetching, screening and the sheet every run; web-search discovery every 2
days, Common Crawl every 3, the sponsor register every 7. A step that isn't
due is skipped and says when it last ran. Sources are fetched up to
`daily_run.max_parallel_sources` at a time (different sites; each site's own
request gap still applies): job boards and feeds first, then the employer
advert lookup, then the company boards.

### Running it automatically

```bash
job-hunt schedule set --at 3am --at 9pm          # job-hunt run every day at 03:00 and 21:00
job-hunt schedule set --times-per-day 3 --first 03:00   # 03:00, 11:00, 19:00
job-hunt schedule set --at 03:00 --run-on-wake   # a run missed while asleep happens on waking
job-hunt schedule status                         # times, slots run, whether checks are running
job-hunt schedule off                            # stop
```

The system's own scheduler starts `job-hunt schedule check` at each run time, and every 5 minutes as a backup:
launchd on macOS (through `~/Applications/JobHuntScheduler.app`, so macOS
can let it read ~/Documents; allow it once when asked), a systemd user timer
on Linux (`loginctl enable-linger` to run while logged out), Task Scheduler
on Windows. A check runs `job-hunt run` only when a slot is due. A slot
missed while the computer slept or was off is skipped, unless
`--run-on-wake` is given: then it runs at the first check after the computer
is back (several missed slots are covered by one run). The computer is never
woken for a run. Each run waits for the internet, keeps the computer from
sleeping until it ends (a closed lid on battery still sleeps; the run then
carries on when it wakes), has a 4-hour limit on awake time, and is retried up to 3
times if it fails. Logs: `logs/scheduled/<date>.log`; a notification shows
each outcome.

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
job_sources/job_boards/  Indeed, LinkedIn, Glassdoor (JobSpy); Google Jobs (SerpApi);
                         Adzuna, Reed (SearchApiSource); HN Who is hiring
job_sources/ats_boards/  Greenhouse, Lever, Ashby, Workable, SmartRecruiters; board link finder
job_sources/advert_pages/ AdvertPageSource (JobPosting markup); The Intake, Student Circus, jobs.ac.uk
job_sources/email_alerts/ Gmail reader and the job links it recognises
job_sources/portals/     logged-in portals in a saved browser profile; KCL Target Connect
job_pipeline/            JobFetchRun (sources -> saved jobs), DailyRun (the daily steps),
                         listings (one per advert across sources), source health
sponsor_register/        finds and reads the Home Office register of licensed sponsors
company_discovery/       seed list; board search (web search); crawl discovery (Common Crawl)
company_discovery/common_crawl/  reads Common Crawl's URL index files by byte range
company_discovery/web_search/  SearchProvider + factory; ddgs (free, no key)
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

- **Job source**: subclass `JobSource` (or `JobSpySource` / `SearchApiSource` /
  `AdvertPageSource` / `AtsBoardSource`),
  decorate with `@register_job_source`, add a block under `job_sources` in
  settings.yaml and its model in `settings_schema.py`. A test checks every
  source has settings and every settings block has a source.
- **ATS**: subclass `AtsBoardSource`, set `ats_platform` (add it to
  `AtsPlatform`; no migration needed) and `search_domains`, and implement
  `board_slug_from_url`, so links to the new ATS are recognised and searched.
- **Database or cache backend**: subclass `DatabaseBackend` / `CacheBackend` in
  its `backends.py` and register it.
- **Search provider**: subclass `SearchProvider`, decorate with
  `@register_search_provider`, add its name to `company_discovery.search_provider`.
  An ATS reader's `search_domains` are searched automatically.
- **Command**: subclass `CliCommand` and decorate with `@register_cli_command`.
