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

## 6. Weil, Gotshal & Manges — BUILD (JSON listing endpoint with HTML snippets)

Recon 18 Nov 2026, 23:03.

- The directory page `https://www.weil.com/people` fills its results by JavaScript from a JSON endpoint:
  `GET https://www.weil.com/WeilPeopleListing/Execute?pagenum=1&pagesize=10&itemId=%7B2186BD5B-A7D8-40E1-87CD-9246016A7816%7D&param_practices_sm%7Cprimary_practice_s%7Cadditional_primary_practices_sm=%7B2F2727CC-4E6C-443B-8777-5FE134DFDDB5%7D%2C%7BC4F4F120-2CEF-4831-B05F-CE442ECB0789%7D&param_role_s=%7B9B7FDA9B-B476-422F-9F30-68DC43AC4126%7D`
  - `itemId` = the listing's own ID (required).
  - The two GUIDs in `param_practices_sm|primary_practice_s|additional_primary_practices_sm` are combined (OR): `{2F2727CC-4E6C-443B-8777-5FE134DFDDB5}` = **Mergers & Acquisitions** and `{C4F4F120-2CEF-4831-B05F-CE442ECB0789}` = **Private Equity**. Identified on 18 Nov by querying one GUID at a time: M&A returned 51 partners, all tagged M&A; PE returned 79, all tagged PE; 38 are in both (51 + 79 − 38 = 92).
  - `param_role_s` = **Partner**.
  - `pagenum` / `pagesize` page the results. `pagesize=100` works: one page holds all 92.
- Response: `TotalCount` (**92**), `TotalPages`, and `SearchGroups`: a "Leadership" group plus A–Z groups. Each group's `SearchResultItemsAsHtml` is a list of HTML snippets, one per person:
  - name: `h3 a`; profile link: its `href`, `/people/<slug>`;
  - title: `p.ppl-item-status b`; offices: the `/locations/...` links in `p.ppl-item-status`;
  - practices: the links in `p.ppl-item-categories`.
- Snippets also hold a photo, a phone number (`p.ppl-item-tel`) and a bio extract (`div.termhighlights`): never read.
- **Leadership repeats people:** 97 snippets but 92 unique profile URLs. All 5 Leadership entries also appear, identically, in the A–Z groups. Dedupe by profile URL.
- Titles: 91 "Partner", 1 "Executive Partner" (the role filter is Partner, so it counts). 8 people list two offices. Practice tags go beyond M&A/PE (e.g. Sustainability & ESG, Capital Markets), but every person matched the M&A/PE filter.
- robots.txt: the endpoint is allowed. Only `/Print?`, `/VCard/`, `/sitecore/` and some media paths are disallowed.
- **Expected: 92 partners.**

## Others — not assessed

Kirkland & Ellis, Latham & Watkins, Skadden, Gibson Dunn, Paul Hastings, King & Spalding, Paul Weiss, Milbank, Sullivan & Cromwell. List in config as `enabled: false`, status "not yet assessed".

---

## Real-world validation

Known facts checked against Orbit's stored Cravath snapshot (run 5, 18 Nov 2026, 65 partners).

- **Aaron M. Gruber** (Cravath co-head of M&A): independently confirmed present in our Cravath list (New York, `person_key` 2242).
- **Iliana Karaoglan:** Weil → Simpson Thacher (Jun 2025) → Weil (Jun 2026) → Cravath (confirmed on cravath.com, 18 Nov 2026). Present in our Cravath list (New York, `person_key` 35943). Weil profile status: _TBC_.
