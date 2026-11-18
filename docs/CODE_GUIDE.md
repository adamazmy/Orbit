# Orbit code guide

A plain-English walkthrough of the code, for someone reading it for the first
time. It is updated at the end of every build step. The README explains *why*
Orbit is built the way it is; this guide explains *how* the code does it.

Status: **two adapters wired end to end (Cravath, Weil)**. `orbit run` fetches
both firms' partners and stores them as a snapshot (on 18 Nov: Cravath 65,
Weil 92). No normalisation, health checks, diff or report yet.

## Run flow

What `orbit run` does today:

1. Load and validate `config/firms.yaml` (`models.load_config`).
2. Open `data/orbit.db`, creating the file and tables if needed (`store.connect`).
3. Copy the firm list into the `firms` table (`store.upsert_firms`).
4. Create a row in `runs` (`store.create_run`).
5. Open one polite `Fetcher` for the run. For each enabled firm
   (`cli.collect_firm`):
   - look up its adapter in the registry. With no adapter, the firm is
     recorded as SKIPPED;
   - call `fetch_partners()`;
   - on success, append the partners to `observations` and record the firm as
     OK with its partner count;
   - on `FetchBlocked`, record BLOCKED; on `FetchFailed`, `AdapterError` or any
     unexpected error, record FAILED, with the reason in `firm_runs.message`.
     Nothing is stored for that firm, so a failure can never look like an
     empty firm;
   - commit, so each firm is saved on its own.
6. Stamp the run finished (`store.finish_run`) and print one line per firm.

Raw responses land in `data/raw/<run_id>/<firm_id>/`.

Still to slot in, between fetching and storing: normalise, then the health
check (0 partners, or a >30% drop, makes a firm SUSPECT), then the diff
against the last OK run. Until the health check exists, "OK" only means
"fetched and parsed without error".

## Files

### `pyproject.toml`
Package metadata and dependencies. Only libraries approved in `CLAUDE.md` are
listed; `pytest` is an optional `dev` extra. `[project.scripts]` maps the
`orbit` command to the Typer app in `orbit/cli.py`, so after installing the
package, `orbit run` works from the terminal. Python is pinned to 3.12.
`selectolax` needs at least 1.0.2, the version tested with the Weil adapter:
it provides `selectolax.lexbor`, while 1.0 removed the old default parser.

### `config/firms.yaml`
The list of all 15 firms. Each entry has an `id` (the slug used everywhere,
including database keys), `name`, `enabled`, a `status` and, unless the firm is
ACTIVE, a one-line `reason`.

- **ACTIVE** (enabled): Cravath, Weil.
- **BLOCKED**: Simpson Thacher (robots.txt disallows its directory API),
  Wachtell (Cloudflare bot challenge on `/attorneys`; evidence in
  `docs/evidence/wachtell-cloudflare.txt`) and Davis Polk (the Pantheon edge
  returns 403 to non-browser TLS clients even with honest headers; evidence in
  `docs/evidence/davispolk-curl-200.txt`). Their `source:` blocks are kept so
  they can be switched back on if the block is lifted.
- **OUT_OF_SCOPE**: Quinn Emanuel (no transactional M&A practice).
- **NOT_ASSESSED**: the other nine.

The `source:` block holds everything firm-specific an adapter needs (listing
URLs, Algolia app id and index, Cravath's position and practice IDs, Weil's
listing, practice and role GUIDs). That
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
- `save_observations` takes the normalised names as a separate, optional list,
  because name normalisation will live in `normalise.py`, not in the store.
  Until that exists, nothing is passed and `name_norm` is stored as NULL.
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
The registry: a plain dict from firm id to adapter class. It currently holds
two entries, `"cravath": CravathAdapter` and `"weil": WeilAdapter`. It's
explicit on purpose, with no auto-discovery: you can see every adapter in one
place.

### `orbit/adapters/cravath.py`
Cravath's directory page is drawn by JavaScript, but the data comes from a
plain JSON endpoint, `/_site/search`, which robots.txt allows. The adapter asks
it for the Partner position (`po=5499`) in the Corporate practice (`pr=3275`),
taking the endpoint URL and both IDs from `firms.yaml`. It makes one request
with `s=100`. If the reported `total` is larger than what came back, it pages
on with the offset `f` until everyone is collected. Today all 65 partners fit
in that one request.

How a hit becomes a `PartnerRecord`:
- `person_key` is Cravath's own `_id`, stable across runs.
- `title` is the hit's title. Two partners (e.g. the Presiding Partner) have
  none, so they fall back to `position_title` from config ("Partner"), because
  the endpoint files them under the Partner position.
- `practices` is always `["Corporate"]` (`practice_name` in config), because
  hits carry no practice field: everyone returned is in the practice we asked
  for. Cravath has no M&A sub-practice, so this over-includes capital markets
  and finance partners. That's a known gap, noted in RECON.md.
- `offices`: hits contain office IDs only, next to phone and fax numbers that
  we never read. `office_names` in config maps them (5413 New York, 5412
  London, 23913 Washington, D.C.). The mapping came from each office's phone
  dialling code in the 18 Nov response. An unknown ID becomes
  `Unknown office (<id>)` with a warning, rather than failing the firm.
- `profile_url` is `profile_base_url` plus the hit's relative `url`.

Partner only: after collecting, `keep_partners` drops anyone whose title
isn't exactly "Partner" (e.g. Of Counsel), and logs how many were dropped. The
plan and CLAUDE.md put this partner filter in shared normalise code. It sits
in the adapter for now because `normalise.py` doesn't exist yet, and should
move there when it does.

Safety checks, all raising `AdapterError` so the run records the firm as
FAILED instead of storing a misleading list:
- the response isn't JSON, or isn't shaped as expected;
- fewer people were collected than the endpoint's `total` (a partial list
  would later look like partners leaving);
- no partners at all are left. The adapter never returns an empty list,
  because zero means the site or our query changed, not that everyone left.

The parsing is in plain functions (`parse_search_response`, `hit_to_record`,
`office_names`, `keep_partners`) so tests can run them on a saved file without
a network.

### `tests/test_adapter_cravath.py` and `tests/fixtures/cravath/`
The fixture keeps the exact structure of the 18 Nov response, cut down to the
fields the adapter reads, with fictional people ("Jane Example", "John
Sample", "Alex Placeholder") and made-up IDs and profile URLs. The office IDs
are the real ones. Emails, phones, photos and bios are removed. Between them
the 3 hits cover all three offices and a partner with no title. It's kept
small and fictional on purpose: a fixture should test parsing, not publish
real people or become a copy of the directory.

The 13 tests run with no network (`pytest`) and cover:
- field mapping, the office map and the title fallback;
- unknown offices, and a badly shaped response or hit;
- the Partner-only filter;
- fetching, using a fake fetcher in place of the real one: one request when
  everything fits, paging when the total is larger, failing on an incomplete
  list, and never returning an empty list.

### `orbit/adapters/weil.py`
Weil's directory page (`weil.com/people`) fills its results by JavaScript from
a JSON endpoint, `/WeilPeopleListing/Execute`, which robots.txt allows. The
adapter sends:
- `itemId` (the listing's own ID);
- the two practice GUIDs, joined with a comma so they combine as OR:
  `{2F2727CC-…}` is Mergers & Acquisitions and `{C4F4F120-…}` is Private Equity.
  This was checked by querying one GUID at a time: every result carried that
  practice's tag;
- the Partner role GUID;
- `pagenum` and `pagesize`.

The endpoint URL, the two parameter names with GUIDs in them, and every GUID
come from `firms.yaml`.

The response is JSON, but each person inside it is an HTML snippet. `TotalCount`
and `TotalPages` describe the whole result. `SearchGroups` holds a
"Leadership" group plus A–Z groups, and each group's `SearchResultItemsAsHtml`
is a list of snippets. Each snippet is parsed with selectolax's `lexbor`
parser. selectolax 1.0 removed its old default parser, so `selectolax.parser`
no longer works.

How a snippet becomes a `PartnerRecord`:
- `name` is the text of `h3 a`, and `profile_url` is `profile_base_url` plus
  that link's `/people/<slug>`.
- `person_key` is the slug, e.g. `jane-doe`. It's taken from the profile URL,
  which is stable.
- `title` is `p.ppl-item-status b`. It's kept as given: the role filter already
  asks for partners, and one person is titled "Executive Partner".
- `offices` are the `/locations/...` links in `p.ppl-item-status`. Some people
  have two.
- `practices` are the links in `p.ppl-item-categories`, kept as listed. They can
  include tags beyond M&A/PE, such as "Sustainability & ESG".
- The photo, phone line (`p.ppl-item-tel`) and bio extract
  (`div.termhighlights`) are never read.

Paging and dedupe: page 1 is requested with `pagesize=100`, which today holds
all 92 partners. If `TotalCount` isn't reached, the adapter asks for the next
`pagenum`. It stops at `TotalPages`, or when a page adds nobody new. The
Leadership group repeats people who also appear under their letter (97
snippets for 92 people on 18 Nov), so records are kept once per profile URL.

Safety checks, all raising `AdapterError`:
- the response isn't JSON, or is missing `TotalCount`, `TotalPages` or
  `SearchGroups`;
- a snippet has no name, title or `/people/` link;
- the unique count doesn't equal `TotalCount`;
- nobody comes back at all.

The parsing is in plain functions (`parse_listing_response`,
`snippet_to_record`) so tests can run them on a saved file without a network.

Difference from Cravath: Weil has no exact-title filter. Cravath drops anyone
not titled exactly "Partner", which would drop Weil's "Executive Partner". The
shared partner filter, once `normalise.py` exists, should treat titles like
"Executive Partner" or "Managing Partner" as partners, and both adapters should
leave the filtering to it.

### `tests/test_adapter_weil.py` and `tests/fixtures/weil/`
The fixture keeps the exact JSON and snippet HTML of the 18 Nov response, with
three fictional people:
- "Jordan Fictional": two offices and two practices. Appears in Leadership
  *and* under J, as real leaders do.
- "Avery Example": Paris, Private Equity.
- "Sam Sample": titled "Executive Partner".

The phone line is left out of the snippets entirely. The photo path and bio
text are fictional, so the tests can check they never reach a record.

The 10 tests run with no network (`pytest`) and cover:
- parsing every snippet including repeats, and field mapping (two offices,
  several practices, absolute URL, slug as key);
- keeping title variants, and never storing bio or photo text;
- a malformed snippet or response raising `AdapterError`;
- fetching, with a fake fetcher: one request that dedupes Leadership and
  sends the exact config IDs, paging by `pagenum`, failing when the unique
  count doesn't match `TotalCount`, and never returning an empty list.

### `orbit/cli.py`
The `orbit` command, built with Typer. `orbit run` follows the run flow above.
`collect_firm` handles one firm: run its adapter, turn the result or the error
into a status (`FirmOutcome`), store the snapshot only when the status is OK,
record the status, and commit. It catches any unexpected exception too, so a
bug in one adapter can't stop the other firms. Options: `--config` (default `config/firms.yaml`), `--db` (default
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
   page from `tests/fixtures/` without using the network. Trim the fixture to
   a handful of records, remove emails and phone numbers, and replace real
   people with fictional names, IDs and URLs (keeping the structure) before
   committing it. See the Cravath adapter for an example.

Nothing else in the codebase should need to change.
