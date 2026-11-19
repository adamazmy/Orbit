# Orbit code guide

A plain-English walkthrough of the code, for someone reading it for the first
time. It is updated at the end of every build step. The README explains *why*
Orbit is built the way it is; this guide explains *how* the code does it.

Status: **report and demo (step 3), plus a third firm (Milbank)**. `orbit run`
fetches Cravath, Weil and Milbank,
keeps partners only, health-checks each firm, stores the snapshot, diffs each
OK firm against its last OK snapshot, pairs cross-firm moves, stores the
events and writes `reports/latest.html`. `orbit demo` shows the same report
built from fictional data.

Commands:
- `orbit run`: collect, diff, then write `reports/latest.html`.
- `orbit report`: re-render `reports/latest.html` from the database, with no
  network.
- `orbit demo`: write `reports/demo.html` from fictional data in a throwaway
  database.

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
   - on `FetchBlocked`, record BLOCKED; on `FetchFailed`, `AdapterError` or any
     unexpected error, record FAILED, with the reason in `firm_runs.message`.
     Nothing is stored for that firm;
   - on success, `cli.save_snapshot`:
     - keeps partners only (`normalise.filter_partners`);
     - runs the health check against the firm's last OK count
       (`health.check_health`, giving OK or SUSPECT);
     - stores the snapshot with `name_norm` filled (`normalise.normalise_name`);
     - records the status;
   - commit, so each firm is saved on its own.
6. `cli.record_events` runs `diff.diff_run`, then stores the events
   (`store.save_events`):
   - Each OK firm is compared with its last OK snapshot. A firm that isn't OK
     produces no events, and a firm's first OK snapshot is only a baseline.
   - Same `person_key` in both snapshots: OFFICE_CHANGED / TITLE_CHANGED if
     those differ. Only in the new snapshot: JOINED. Only in the old one: LEFT.
   - A LEFT at firm A and a JOINED at firm B are paired when the names match
     (`diff.match_moves`). The pair can be in this run, or one side can be an
     unmatched event from the last 4 weeks.
   - The same normalised name at two OK firms in this run gets a
     LISTED_AT_TWO_FIRMS flag (`diff.find_dual_listings`).
7. Stamp the run finished, write `reports/latest.html` (`report.write_report`),
   and print one line per firm plus an event summary.

Raw responses land in `data/raw/<run_id>/<firm_id>/`.

### Event types
- Facts: JOINED, LEFT, OFFICE_CHANGED, TITLE_CHANGED, and MOVED (an automatic
  cross-firm match).
- Review flags, always `needs_review`:
  - POSSIBLE_MOVE: links a LEFT and a JOINED that might be the same person.
    The LEFT and JOINED themselves stay as facts.
  - LISTED_AT_TWO_FIRMS: the same normalised name at two firms in one run.
    Usually someone whose old firm hasn't removed their profile yet.

### Matching people across firms
`diff.name_score` uses rapidfuzz `token_set_ratio` on normalised names, so a
middle name present at one firm only doesn't block a match.

- **Automatic:** a score of 92 or more **and** identical normalised first and
  last names. One MOVED replaces this run's LEFT/JOINED. The first-and-last-name
  rule exists because short names score high on small differences: "Jane Doe"
  vs "Janet Doe" scores about 94.
- **Uncertain:** any other pair scoring 85 or more. The LEFT and JOINED are
  kept as facts, and a POSSIBLE_MOVE links them for a human to check.
- **Across weeks:** firms update their websites at different speeds, so a
  LEFT or JOINED in this run can also pair with an unmatched JOINED or LEFT
  from runs that started in the last 28 days (`MATCH_WINDOW_DAYS`, using
  `store.get_recent_events`). The earlier event stays stored as it was, and
  the new MOVED records its id as `details["matched_event_id"]`. An event that
  already has a MOVED pointing at it isn't matched again, and two earlier
  events are never paired with each other.

**Known limitation: name changes.** Matching is by name, so a partner whose
name changes while moving (e.g. after marriage) shows up as a separate LEFT
and JOINED, not a MOVED. Bracketed former names such as "(née …)" are
removed, but they're never used to match. A fix would need another identifier
(e.g. a bar number), which we don't collect.

**Listed at two firms** is flagged in every run while it lasts, including a
firm's first (baseline) run, because it's a state worth checking, not a change.

### `orbit/normalise.py`, `orbit/health.py`, `orbit/diff.py`
- `normalise.py`:
  - `is_partner` counts titles containing "partner" (Executive, Managing,
    Presiding…), except those with counsel, associate, retired, former or
    emeritus;
  - `normalise_name` lowercases, strips accents, removes bracketed parts such
    as "(née …)", honorifics, suffixes (Jr., III) and single-letter initials,
    and maps a short list of unambiguous nicknames (Bill→William, Bob→Robert).
- `health.py`: 0 partners, or a drop of more than 30% versus the last OK run,
  makes a firm SUSPECT. SUSPECT snapshots are stored, for the record, but
  never diffed.
- `diff.py`: pure functions (`diff_firm`, `match_moves`, `find_dual_listings`,
  `name_score`), plus `diff_run` and `unmatched_recent_events`, which read
  from the store.
- Tests:
  - `tests/test_normalise.py`, `tests/test_health.py` and `tests/test_diff.py`
    (pure functions);
  - `tests/test_run_flow.py` (multi-run scenarios on a temporary SQLite file,
    with run dates set explicitly):
    - the first run is a baseline;
    - a cross-firm move gives exactly one MOVED;
    - a move spread over two weeks is a MOVED, but one six weeks apart stays
      LEFT plus JOINED;
    - an earlier event that's already matched isn't reused;
    - two firms listing the same person are flagged;
    - a drop from 90 to 10 partners is SUSPECT with zero events;
    - the run after a SUSPECT run diffs against the last OK run.

All tests use fictional people.

Partner filtering moved out of the Cravath adapter into this shared step.
Adapters now return every person they parse.

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
- `save_observations` takes the normalised names as a separate list, because
  name normalisation lives in `normalise.py`, not in the store. `orbit run`
  always passes them. Snapshots stored before step 2 (runs 1–6) have
  `name_norm` NULL.
- `get_last_ok_run_id` exists because diffs compare against the last *OK* run
  for a firm, not simply the previous run. `get_last_ok_partner_count` gives
  that run's partner count, for the health check.
- `save_events` / `get_events` write and read the `events` table, using the
  `Event` model from `models.py`. `details` is stored as JSON, and events read
  back carry their database `id`. `get_recent_events` returns events from runs
  that started within N days before a given run, for cross-week move matching.
  The `type` column also holds the two review flags, POSSIBLE_MOVE and
  LISTED_AT_TWO_FIRMS, beyond the five types in PLAN.md.
- `create_run` takes an optional `started_at`, so the demo can date its two
  fictional runs a week apart. `get_run_started_at` and `get_latest_run_id`
  serve the report.

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

URLs are built by `build_url(url, params)`. A URL with no `params` is used
exactly as given; otherwise the params are added to its existing query.
Non-obvious: `httpx.URL(url, params=...)` *replaces* the query, even with
`params=None`, which silently dropped Milbank's filters during recon (found 19
Nov; Cravath and Weil were unaffected because they pass everything as params).

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

The adapter returns everyone it parses. Deciding who counts as a partner is
the shared step's job (`normalise.filter_partners`), as for every firm.

Safety checks, all raising `AdapterError` so the run records the firm as
FAILED instead of storing a misleading list:
- the response isn't JSON, or isn't shaped as expected;
- fewer people were collected than the endpoint's `total` (a partial list
  would later look like partners leaving);
- nobody came back at all. The adapter never returns an empty list, because
  zero means the site or our query changed, not that everyone left.

The parsing is in plain functions (`parse_search_response`, `hit_to_record`,
`office_names`) so tests can run them on a saved file without a network.

### `tests/test_adapter_cravath.py` and `tests/fixtures/cravath/`
The fixture keeps the exact structure of the 18 Nov response, cut down to the
fields the adapter reads, with fictional people ("Jane Example", "John
Sample", "Alex Placeholder") and made-up IDs and profile URLs. The office IDs
are the real ones. Emails, phones, photos and bios are removed. Between them
the 3 hits cover all three offices and a partner with no title. It's kept
small and fictional on purpose: a fixture should test parsing, not publish
real people or become a copy of the directory.

The 12 tests run with no network (`pytest`) and cover:
- field mapping, the office map and the title fallback;
- unknown offices, and a badly shaped response or hit;
- returning every title untouched, for the shared partner filter;
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

Like Cravath, the adapter leaves partner filtering to the shared step, which
counts "Executive Partner" as a partner.

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

### `orbit/adapters/milbank.py`
Milbank's directory uses the **same search platform as Cravath**:
`/_site/search` with `v=attorney` and the offset `f`. Here it's asked for
rendered cards (the bare `html` flag), so the answer is `json_wrapped_html`:
JSON holding `totals.ALL` and an HTML string of `li.card--attorney` cards.

Non-obvious decisions:
- **One request per practice.** Repeated `p=` filters are combined as AND, not
  OR: on 19 Nov, Private Equity (305), M&A (306) and Distressed M&A (823)
  together returned 9, exactly the people in all three. Queried separately
  they return 28, 53 and 15, which merge to 59. So the adapter queries each
  practice in `firms.yaml`, merges by profile URL, and gives each person the
  practices whose query returned them (the cards themselves carry none).
  Corporate (282) is the parent practice and isn't used.
- **Completeness per practice.** Each practice's unique count must equal its
  `totals.ALL`, or the firm fails rather than looking short. A page shorter
  than `page_size` is the last one, so no extra request is made.
- **The URL is built by hand**, because `html` must be sent as a bare flag;
  encoding it as a parameter would send `html=`. `q=Partner` (used by the
  site's own page) isn't sent: the position filter `t=20012` gives the same
  count without it.
- `person_key` is the profile slug (`/en/professionals/<slug>.html`).
- Only the name, title, office and profile link are read. Cards also hold an
  email, a phone number and a photo.

**Shared platform, not shared code (yet).** Milbank and Cravath both run
`/_site/search`, but their filters and response modes differ (Cravath: `po`
and `pr` with `raw_json` hits; Milbank: `t` and `p` with HTML cards). If a third firm on
this platform appears, that's the trigger to write one configurable
`/_site/search` adapter in `adapters/base.py` or its own module. Building it
now, from two examples, would mean guessing its shape.

### `tests/test_adapter_milbank.py` and `tests/fixtures/milbank/`
The fixture keeps the card HTML structure of the 19 Nov response with three
fictional people (Riley Q. Placeholder, Morgan Example, Casey Sample). The
email and phone blocks are left out entirely; the photo path is fictional, so
the tests can check it never reaches a record.

The 10 tests run with no network and cover parsing and field mapping,
rejecting the wrong response mode or a card without a profile link, one
request per practice with the exact filters (no `q`, bare `html`), merging
practices by profile, paging by offset, failing when a practice's unique
count doesn't match `totals.ALL`, and never returning an empty list.

### `orbit/cli.py`
The `orbit` command, built with Typer. `orbit run` follows the run flow above.
- `collect_firm` runs one firm's adapter. If the adapter fails, it records the
  firm as BLOCKED or FAILED. It catches unexpected exceptions too, so a bug in
  one adapter can't stop the other firms.
- `save_snapshot` handles the records an adapter returned: partner filter,
  health check, store, then commit.
- `record_events` diffs the run and stores the events.

`orbit report` and `orbit demo` are described below. Options: `--config`
(default `config/firms.yaml`), `--db` (default `data/orbit.db`), `--output`
(default `reports/latest.html`, or `reports/demo.html` for the demo), and `-v`
for debug logs. Paths are relative to the folder you run from, normally the
repo root.

### `orbit/report.py` and `templates/report.html.j2`
`write_report` renders the most recent run into a single HTML file. CSS and
about 20 lines of vanilla JS are inline, and nothing loads from a CDN, so the
file opens offline and can be emailed. Sections, in order:
1. **Header:** run date, firms tracked, partners per firm. Each chip's count
   is the number of rows the partners table shows for that firm, so the two
   never disagree. For a SUSPECT or FAILED firm that's the last good count,
   with this run's figure as the warning: "Weil, Gotshal & Manges: 92
   partners (last good, run 9) · SUSPECT: this run returned 51". The health
   table still shows this run's count and reason.
2. **This week's changes:** MOVED first, then JOINED, LEFT, office and title
   changes.
   - A firm whose first OK snapshot is this run gets "baseline recorded,
     changes appear from the next run".
   - POSSIBLE_MOVE and LISTED_AT_TWO_FIRMS are shown separately under "Needs
     review", worded as doubts, never as facts.
3. **Firm health:** every firm in config. Firms collected this run show their
   status and reason (OK, SUSPECT, FAILED…). The others show their config
   status and reason (BLOCKED, OUT OF SCOPE, NOT ASSESSED).
4. **All partners:** each firm's latest OK snapshot (`snapshot_runs`). For a
   SUSPECT or FAILED firm that's its last good list, not the doubtful or
   missing one. So stale data is never mistaken for current, `stale_notes`
   labels such a firm above the table: "Showing last good
   snapshot (run N), because this run looked broken" (SUSPECT), or "…because
   this run failed" (FAILED). The table has a text search, plus firm, office
   and practice drop-downs that filter rows in place.

The Python side (`build_report_data`, `describe_event`, `health_rows`…) does
the grouping and wording, so the template only lays things out. Jinja2
autoescaping is on, because names and titles come from websites. A test
checks that a name containing `<script>` is escaped.

### `orbit/demo.py`
`orbit demo` exists because a fresh install has only one snapshot, which
shows no changes. The demo plays two fictional weekly runs (12 and 19 Nov)
through the real pipeline (`save_snapshot`, `record_events`), so the report
shows real engine output. What happens between the two weeks:
- a cross-firm MOVED (Ilona Fictional);
- a POSSIBLE_MOVE (Jane Doe → "Janet Doe"), with its LEFT and JOINED kept;
- a JOINED and a LEFT;
- an office change;
- a SUSPECT firm (10 → 2 partners, so no events from it);
- a BLOCKED firm in the health panel.

Everything happens in a `tempfile.TemporaryDirectory` database that's
deleted afterwards. The real `data/orbit.db` is never opened, and a test spies
on `store.connect` to prove it. The report carries the banner "DEMO —
fictional data showing how weekly changes appear." `orbit/cli.py` imports the
demo inside the `demo` command, not at the top, because the demo reuses the
CLI's pipeline functions.

### `tests/test_report.py`
Builds reports from the fictional demo weeks and checks:
- section order, and MOVED appearing first;
- review flags kept apart and labelled;
- health showing both SUSPECT and config-blocked firms;
- a SUSPECT firm's partner list coming from its last good run;
- the filters and profile links;
- no external scripts or styles;
- the baseline message, and the "no runs yet" page;
- HTML escaping of names;
- that the demo never opens or changes the real database.

### `.gitignore`
Keeps `data/` (raw page cache and the database), any `*.db` file and
`reports/` out of git. `reports/latest.html` lists real scraped partners, so
it follows the same rule as the database. It also ignores Python build and
cache folders, and local tool config files that contain tokens.

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

Before writing a new adapter, check whether the firm's site runs a platform
Orbit already reads (e.g. `/_site/search`, used by Cravath and Milbank).
Milbank, added this way on 19 Nov, needed only these four steps, plus one
fetcher fix found on the way (`build_url`).

Nothing else in the codebase should need to change.
