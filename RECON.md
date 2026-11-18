# RECON.md — how each firm's directory works

Recon done by hand on 18 Nov 2026. This is the source of truth for building adapters.

**Scope rule (all firms):** keep people titled **Partner** in **M&A, Private Equity, or M&A sub-practices**. Dedupe within a firm by stable ID. Keep only: name, title, practices, offices, profile URL, stable ID. **Drop** emails, phones, photos, bios, education, everything else.

---

## 1. Wachtell, Lipton, Rosen & Katz — BLOCKED (static HTML; Cloudflare bot challenge)

- WordPress site. Names are in the **raw HTML** (no JS needed, no JSON feed).
- Filters are URL query params: `asf_p` = position, `asf_pa` = practice area.
- Fetch two pages (no pagination, everything on one page):
  - Corporate: `https://www.wlrk.com/attorneys?asf_n=&asf_pa=corporate&asf_ls=&asf_p=partner&asf_ugs=`
  - Real Estate M&A: `https://www.wlrk.com/attorneys?asf_n=&asf_pa=real-estate-ma&asf_ls=&asf_p=partner&asf_ugs=`
- Profile URLs mostly `/attorney/<handle>/`, but some are `/<handle>/` (e.g. `/msjones/`). Use the profile URL as the stable ID; don't assume the pattern.
- One office (New York).
- robots.txt: only disallows `/wp-admin/`, `/files/`, `/webdocs/`, `/docs/`. `/attorneys` allowed.
- **Cloudflare bot challenge (found 18 Nov, after recon):** a non-browser GET of `/attorneys` returns `HTTP 403` with `Cf-Mitigated: challenge` and `Server: cloudflare`. Orbit's own fetcher hit the same 403. Not bypassed (scraping conduct rule). Evidence: [docs/evidence/wachtell-cloudflare.txt](docs/evidence/wachtell-cloudflare.txt).
- **Ground truth: 35 partners** (34 Corporate + 1 Real Estate M&A, no overlap), hand-counted 18 Nov.
- Sitemap (`/sitemap_index.xml`) has per-profile last-modified dates — future incremental-update idea, not needed now.
- Test case: Alison Preiss must NOT appear (left for Simpson Thacher, Jan 2026).

## 2. Davis Polk & Wardwell — BLOCKED (Algolia search API; Pantheon edge 403)

- Directory at `https://www.davispolk.com/lawyers` is powered by **Algolia**.
- Endpoint: `POST https://dbc8kua830-dsn.algolia.net/1/indexes/*/queries`
- App ID: `DBC8KUA830`. Sent as URL params `x-algolia-application-id` and `x-algolia-api-key`.
- **The API key is a secured, expiring key** (decoded: restricted to `lawyer_search_en`, `validUntil` ≈ a day). **Never hard-code it.** Each run: GET `/lawyers`, extract the current key from the page source (search for `x-algolia-api-key` / `algolia` / an `apiKey` in inline JS or `drupalSettings`), then call the API. If the key can't be found → firm status FAILED, not zero partners.
- Index: `lawyer_search_en`. Body params use `facetFilters`, `page`, `hitsPerPage`.
- One query for the whole target set: `facetFilters=[["practice:Mergers & Acquisitions","practice:Private Equity"],["job_title:Partner"]]` (inner list = OR, outer = AND). Raise `hitsPerPage` (e.g. 100) and loop `page` until `nbPages`.
- Hit fields to keep: `objectID` (stable ID, e.g. `entity:node/57116:en`), `name`, `job_title`, `office`, `practice`, `url` (relative, prefix `https://www.davispolk.com`).
- Messy data: `office` can be a string **or a list** (`["São Paulo","New York"]`); practice values can have trailing spaces (`"Public Company Advisory "`); a custom title like "Managing Partner" can appear.
- robots.txt: `/lawyers` allowed; `/search*`, `/vcard/*`, `*.pdf` disallowed (never fetch those). API is on algolia.net (not governed by firm robots.txt) — same politeness applies.
- **Expected: 80 partners** (58 M&A + 55 PE − 33 overlap).
- **Blocked for Orbit's fetcher (found 18 Nov, after recon):** both results below are from the same machine with the same User-Agent (`OrbitBot/0.1 (partner-market research; weekly; +https://github.com/adamazmy/Orbit)`):
  - `curl.exe` gets **200** on `/robots.txt` and `/lawyers`, served from the CDN cache (`Via: varnish`, `X-Cache: HIT`).
  - Orbit's fetcher (httpx on Python's OpenSSL) gets **403** from the edge (`Server: Pantheon`, `x-pantheon-serious-reason: The page could not be loaded properly.`) on `/robots.txt`, in three tries: the default httpx headers, then with honest `Accept` / `Accept-Language` added, then with exactly curl's headers (Host, User-Agent, `Accept: */*` only).
  - With the headers identical, the remaining difference is the TLS client, so we treat this as TLS fingerprinting. **Not bypassed:** no browser or TLS impersonation. Status set to BLOCKED in `config/firms.yaml`. `/lawyers` was never fetched by Orbit and the Algolia key was never read.
  - Evidence: [docs/evidence/davispolk-curl-200.txt](docs/evidence/davispolk-curl-200.txt).

## 3. Cravath, Swaine & Moore — BUILD (JSON GET)

- Directory renders with JS; names NOT in raw HTML.
- Endpoint: `GET https://www.cravath.com/_site/search?po=5499&pr=3275&s=20&f=0&v=attorney`
  - `po` = position ID (5499 = Partner)
  - `pr` = practice ID (3275 = Corporate)
  - `s` = page size (try 100), `f` = offset, `v=attorney`
- Response: `{mode: "raw_json", hits: {"5499": {total, hits: [...]}}}`. Each hit has `_id` (stable ID), `name`, `title`, `url` (e.g. `/people/michael-j-aiello.html`), `offices_info`, `template_keyword: "attorney"`.
- Drop: `email`, photos, `main_content` (bio), `educations`, `bar_admissions`, `clerkships`.
- Corporate has no sub-practices on Cravath's site → Corporate is broader than M&A (includes capital markets/finance). Known over-inclusion; document it.
- robots.txt: only disallows `/people/index.html?` (filtered HTML directory). `/_site/search` is NOT disallowed. Never request `/people/index.html?...`.
- **Expected: 65 partners.**

## 4. Simpson Thacher & Bartlett — BLOCKED (document, don't build)

- Found a clean JSON API (`POST /dataservices/.../AttorneyLookup/TeamMembers`), but **robots.txt disallows `/dataservices`**. Not used.
- Permitted fallback `sitemap-directory.xml` is **stale**: every `lastmod` is 2021-02-24; recent hires (Alison Preiss, Nick Appleton) missing; current partners still under `/associates/`.
- Live site confirms Alison Preiss and Nick Appleton are partners there (useful as cross-firm-move facts for the README).
- Future routes: ask the firm, license data (e.g. Leopard List), or a manual check.

## 5. Quinn Emanuel — SCOPE NOTE

- Litigation-only firm: no transactional M&A practice, only M&A *litigation*. Excluded by the scope rule; explain in README.

## Others — not assessed

Kirkland & Ellis, Latham & Watkins, Skadden, Gibson Dunn, Paul Hastings, King & Spalding, Paul Weiss, Milbank, Sullivan & Cromwell, Weil. List in config as `enabled: false`, status "not yet assessed".

---

## Real-world validation

Known facts checked against Orbit's stored Cravath snapshot (run 5, 18 Nov 2026, 65 partners).

- **Aaron M. Gruber** (Cravath co-head of M&A): independently confirmed present in our Cravath list (New York, `person_key` 2242).
- **Iliana Karaoglan:** Weil → Simpson Thacher (Jun 2025) → Weil (Jun 2026) → Cravath (confirmed on cravath.com, 18 Nov 2026). Present in our Cravath list (New York, `person_key` 35943). Weil profile status: _TBC_.
