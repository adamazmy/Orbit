# Orbit code guide

A plain-English walkthrough of the code, for someone reading it for the first
time. It is updated at the end of every build step. The README explains *why*
Orbit is built the way it is; this guide explains *how* the code does it.

Status: **step 2 (skeleton)**. `orbit run` loads the config and records an
empty run. No adapters, normalisation, health checks, diff or report yet.

## Run flow

What `orbit run` does today:

1. Load and validate `config/firms.yaml` (`models.load_config`).
2. Open `data/orbit.db`, creating the file and tables if needed (`store.connect`).
3. Copy the firm list into the `firms` table (`store.upsert_firms`).
4. Create a row in `runs`, then stamp it finished (`store.create_run`, `store.finish_run`).
5. Print the run id and which enabled firms have an adapter registered.

Where later steps slot in, between creating and finishing the run: for each enabled firm, look up
its adapter in the registry, fetch through the polite fetcher, normalise,
health-check, store the snapshot and diff against the last OK run.

## Files

### `pyproject.toml`
Package metadata and dependencies. Only libraries approved in `CLAUDE.md` are
listed; `pytest` is an optional `dev` extra. `[project.scripts]` maps the
`orbit` command to the Typer app in `orbit/cli.py`, so after installing the
package, `orbit run` works from the terminal. Python is pinned to 3.12.

### `config/firms.yaml`
The list of all 15 firms. Each entry has an `id` (the slug used everywhere,
including database keys), `name`, `enabled`, a `status` and, unless the firm is
ACTIVE, a one-line `reason`.

- **ACTIVE** (enabled): Wachtell, Davis Polk, Cravath.
- **BLOCKED**: Simpson Thacher (robots.txt disallows its directory API).
- **OUT_OF_SCOPE**: Quinn Emanuel (no transactional M&A practice).
- **NOT_ASSESSED**: the other ten.

The `source:` block holds everything firm-specific an adapter needs (listing
URLs, Algolia app id and index, Cravath's position and practice IDs). That
keeps URLs and IDs out of Python code, so if a firm changes a practice ID only
the config changes. Values come from `RECON.md`. `expected_partners` is the
hand-counted number from recon, kept for the accuracy check later.

The top-level `user_agent` identifies Orbit to every site and points back to
the GitHub repo.

### `orbit/models.py`
Data shapes, validated with pydantic.

- `PartnerRecord`: one partner as seen in one run. Name, firm, title,
  practices, offices, profile URL and `person_key`. `person_key` is the firm's
  stable ID for that person (profile URL or the site's own ID). It is how the
  diff recognises the same person from one week to the next. Deliberately no
  email, phone, photo or bio fields.
- `FirmConfig` / `OrbitConfig`: the shape of `firms.yaml`. Validation catches
  config mistakes at startup: an enabled firm whose status isn't ACTIVE (that
  could scrape a BLOCKED firm by accident), a non-ACTIVE firm with no reason,
  or a duplicate firm id.
- `load_config()`: reads the YAML and validates it.

### `orbit/store.py`
The SQLite database, using the stdlib `sqlite3` module (no ORM). The schema is
PLAN.md section 4: `firms`, `runs`, `firm_runs`, `observations`, `events`.

Non-obvious decisions:
- **Append-only.** Each run adds a new snapshot to `observations`; nothing is
  overwritten. History is what makes change detection possible.
- `practices` and `offices` are stored as JSON lists in text columns. That's
  simple, and there's no need to query inside them in SQL.
- Two additions to the plan's schema: `firm_runs` has a primary key on
  `(run_id, firm_id)` so a firm can't get two statuses in one run, and
  `observations` has an index on `(run_id, firm_id)` because every diff reads
  that way.
- Functions don't commit; the caller does. That lets one firm's work be saved
  in a single transaction later.
- `save_observations` takes the normalised names as a separate list, because
  name normalisation lives in `normalise.py` (step 3), not in the store.
- `get_last_ok_run_id` exists because diffs compare against the last *OK* run
  for a firm, not simply the previous run.
- There are no event read or write helpers yet. They arrive with the diff
  engine, alongside the event model they need.

### `orbit/fetch.py`
The polite fetcher. Every network request in Orbit goes through `Fetcher`, so
the scraping rules are enforced in one place:

- **robots.txt**: downloaded once per site per run and checked before every
  request. A disallowed URL raises `FetchBlocked`. A missing robots.txt (404)
  means no restrictions. A 401/403 on robots.txt means keep out. If robots.txt
  can't be read at all (network error, 5xx), we don't fetch.
- **User-Agent**: the identifying string from `firms.yaml`, on every request.
- **Delay**: at least 2 seconds between requests to the same domain, tracked
  per domain, so two firms on different domains don't slow each other down.
- **Timeouts**: 10s to connect, 20s overall.
- **Retries**: up to 3 attempts for network errors and 429/5xx, with a growing
  pause between them.
- **Cache**: every response body is written to
  `data/raw/<run_id>/<firm_id>/NNN_<url-slug>.<ext>`, and `_requests.jsonl` in
  the same folder records the URL, method and status of each file. API keys in
  URLs are replaced with `REDACTED` in that log.

Two error types tell the run what to do with a firm: `FetchBlocked` (robots.txt
or HTTP 401/403, so the firm is BLOCKED) and `FetchFailed` (anything else, so the
firm is FAILED).

`post_json(..., robots_gate_url=...)` is the documented exception for Davis
Polk. Its search API lives on algolia.net, so we check davispolk.com's
robots.txt for `/lawyers`, the page the API powers. The same delay and
User-Agent apply.

Note: the raw cache stores whole responses, which can include fields we don't
keep (e.g. emails in Cravath's JSON). `data/` is gitignored and stays local;
the retention policy in the README covers it.

### `orbit/adapters/base.py`
The adapter interface. `Adapter` is an abstract base class holding the firm's
config and the shared fetcher. Subclasses implement one method,
`fetch_partners() -> list[PartnerRecord]`. When something goes wrong, adapters
raise rather than returning an empty list, so a broken scraper can never look
like "every partner left". `AdapterError` covers "the site answered, but not
in the expected shape".

### `orbit/adapters/__init__.py`
The registry: a plain dict from firm id to adapter class, empty for now. It's
explicit on purpose, with no auto-discovery: you can see every adapter in one
place.

### `orbit/cli.py`
The `orbit` command, built with Typer. `orbit run` follows the run flow above.
Options: `--config` (default `config/firms.yaml`), `--db` (default
`data/orbit.db`), and `-v` for debug logs. Paths are relative to the folder
you run from, normally the repo root.

### `.gitignore`
Keeps `data/` (raw page cache and the database) and any `*.db` file out of
git, so real scraped partner data is never committed. It also ignores Python
build/cache folders and local tool config files that contain tokens.

## How to add a firm

1. Add an entry to `config/firms.yaml` with `enabled: true`, `status: ACTIVE`
   and its URLs and IDs under `source:`.
2. Create `orbit/adapters/<firm>.py` with a class that subclasses `Adapter` and
   implements `fetch_partners()`, using `self.fetcher` for every request and
   `self.firm.source` for every URL or ID.
3. Add one line to `ADAPTERS` in `orbit/adapters/__init__.py`.
4. Add one fixture test, `tests/test_adapter_<firm>.py`, that parses a cached
   page from `tests/fixtures/` without using the network.

Nothing else in the codebase should need to change.
