# CLAUDE.md — working rules for this repo

Project: Orbit, a partner market tracker (take-home for Pinpoint Partners). Read PLAN.md and RECON.md first.

## The author must be able to explain every line
- Write boring, readable Python. Plain functions, clear names, type hints, docstrings on public functions.
- No clever one-liners, metaprogramming, decorators-for-everything or abstract base class hierarchies beyond `adapters/base.py`.
- Small files, small functions. If a function exceeds ~40 lines, split it.
- Comments explain *why*, not *what*.

## Scope discipline
- Build only what PLAN.md lists. Do not add features, dependencies or files that aren't in the plan — ask first.
- Approved dependencies: httpx, selectolax (or beautifulsoup4), playwright (only inside an adapter that needs it), rapidfuzz, pydantic, pyyaml, typer, jinja2, pytest. Nothing else without asking.
- No ORMs, no async frameworks, no Docker, no web servers.

## Work in small steps
- One module or one adapter per step. Stop after each step and summarise what changed and why.
- Pure logic (normalise, diff, signals, health) must have pytest tests that run without network.
- Commit after each working step with a clear message.
- At the end of each step, update `docs/CODE_GUIDE.md`: what the new module does, how it fits the run flow, and any non-obvious decision. Plain English, written for someone reading the code for the first time.
- Every module begins with a docstring explaining its purpose.

## Scraping conduct (non-negotiable)
- Every request goes through `orbit/fetch.py`: robots.txt check, identifying User-Agent, ≥2s delay per domain, timeouts, cache raw responses to `data/raw/<run_id>/<firm_id>/`.
- Exception, documented in RECON.md: Davis Polk's Algolia API lives on algolia.net. Gate it on davispolk.com's robots.txt allowing `/lawyers` (the page it powers), and apply the same delay and User-Agent.
- Never bypass CAPTCHAs, logins, bot protection or paywalls. If blocked, mark the firm BLOCKED and move on.
- Collect only: name, title, practices, offices, profile URL. Never emails, phone numbers, photos or bio text.

## Extensibility (adding a firm must be easy)
- Adding a firm = (1) one entry in `config/firms.yaml`, (2) one file in `orbit/adapters/`, (3) one line in the explicit registry dict in `orbit/adapters/__init__.py`, (4) one fixture test. Nothing else in the codebase changes.
- Adapters only fetch and parse: they return `list[PartnerRecord]`. Normalising, health checks, storing, diffing and reporting are shared and never firm-specific.
- Firm-specific values (URLs, practice IDs, position IDs, practice names in scope) live in `firms.yaml`, not in adapter code.
- Use an explicit registry dict, not auto-discovery or plugins.
- Put shared helpers (e.g. paging a JSON endpoint, parsing a listing page) in `adapters/base.py` only once two adapters need them. Don't build generic platform adapters ahead of need.
- Keep `docs/CODE_GUIDE.md`'s "How to add a firm" checklist current.

## Correctness rules
- A firm whose health check fails (0 partners, or >30% drop vs last OK run) produces NO change events that run.
- Diff against the last OK run for that firm, not simply the previous run.
- Fuzzy cross-firm MOVED matches below the auto threshold are flagged needs_review, never silently asserted.

## Style
- Python 3.12, `ruff` defaults for formatting.
- Logging via `logging`, not print (except CLI output).
- Config in `config/firms.yaml`; no hard-coded URLs outside adapters/config.
