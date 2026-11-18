"""The polite fetcher: the only way Orbit talks to the internet.

Every request goes through Fetcher so scraping conduct is enforced in one place
rather than trusted to each adapter:

- robots.txt is checked before every request; disallowed URLs are never fetched.
- Every request carries an identifying User-Agent.
- At least MIN_DELAY_SECONDS between requests to the same domain.
- Timeouts on every request, and a few retries for temporary failures.
- Every raw response body is cached to data/raw/<run_id>/<firm_id>/ so parsers
  can be re-run (and turned into test fixtures) without hitting the site again.

Adapters get two errors to react to: FetchBlocked (we're not allowed in, so the
firm becomes BLOCKED) and FetchFailed (something broke, so the firm is FAILED).
"""

import json
import logging
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

logger = logging.getLogger(__name__)

MIN_DELAY_SECONDS = 2.0
TIMEOUT = httpx.Timeout(20.0, connect=10.0)
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 5.0
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


class FetchError(Exception):
    """Base class for everything the fetcher raises."""


class FetchBlocked(FetchError):
    """We may not fetch this URL: robots.txt disallows it, or the site refused us (401/403)."""


class FetchFailed(FetchError):
    """The request failed for another reason (network error, 5xx, unexpected status)."""


class Fetcher:
    """Polite HTTP client for one run. Use as a context manager so the connection pool closes."""

    def __init__(
        self,
        user_agent: str,
        run_id: int,
        cache_root: Path = Path("data/raw"),
        min_delay: float = MIN_DELAY_SECONDS,
    ) -> None:
        self.run_id = run_id
        self.cache_root = cache_root
        self.min_delay = min_delay
        self.client = httpx.Client(
            headers={"User-Agent": user_agent},
            timeout=TIMEOUT,
            follow_redirects=True,
        )
        self._robots: dict[str, RobotFileParser] = {}
        self._last_request_at: dict[str, float] = {}
        self._cache_counter: dict[str, int] = {}

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.client.close()

    def get(
        self, firm_id: str, url: str, params: dict[str, Any] | None = None
    ) -> httpx.Response:
        """GET a URL after checking robots.txt, and cache the response for this firm."""
        full_url = str(httpx.URL(url, params=params))
        self._check_robots(full_url)
        response = self._send("GET", full_url)
        self._cache(firm_id, "GET", full_url, response)
        return response

    def post_json(
        self,
        firm_id: str,
        url: str,
        body: dict[str, Any],
        params: dict[str, Any] | None = None,
        robots_gate_url: str | None = None,
    ) -> httpx.Response:
        """POST a JSON body after checking robots.txt, and cache the response.

        robots_gate_url: check robots.txt for this page instead of the request
        URL. Only for a firm's search API hosted on a third-party domain (Davis
        Polk's Algolia): we follow the firm's own rules for the page the API powers.
        """
        full_url = str(httpx.URL(url, params=params))
        self._check_robots(robots_gate_url or full_url)
        response = self._send("POST", full_url, json=body)
        self._cache(firm_id, "POST", full_url, response)
        return response

    def _check_robots(self, url: str) -> None:
        """Raise FetchBlocked if the site's robots.txt disallows this URL for us."""
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            self._robots[origin] = self._load_robots(origin)
        user_agent = self.client.headers["User-Agent"]
        if not self._robots[origin].can_fetch(user_agent, url):
            raise FetchBlocked(f"robots.txt disallows {url}")

    def _load_robots(self, origin: str) -> RobotFileParser:
        """Download and parse one site's robots.txt (once per run)."""
        parser = RobotFileParser(f"{origin}/robots.txt")
        try:
            response = self._send("GET", f"{origin}/robots.txt", raise_on_status=False)
        except FetchFailed as error:
            # Can't read the rules, so we can't know we're allowed: don't fetch.
            raise FetchFailed(f"could not read {origin}/robots.txt: {error}") from error
        if response.status_code == 200:
            parser.parse(response.text.splitlines())
        elif response.status_code in (401, 403):
            # Same conservative choice as the stdlib: a protected robots.txt means keep out.
            parser.disallow_all = True
        elif 400 <= response.status_code < 500:
            # No robots.txt (e.g. 404) means no restrictions (RFC 9309).
            parser.allow_all = True
        else:
            raise FetchFailed(
                f"{origin}/robots.txt returned HTTP {response.status_code}"
            )
        return parser

    def _send(
        self, method: str, url: str, raise_on_status: bool = True, **kwargs: Any
    ) -> httpx.Response:
        """Send one request with the per-domain delay, retrying temporary failures."""
        last_problem = ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if attempt > 1:
                time.sleep(RETRY_BACKOFF_SECONDS * (attempt - 1))
            self._wait_for_domain(urlsplit(url).netloc)
            try:
                response = self.client.request(method, url, **kwargs)
            except httpx.TransportError as error:
                last_problem = f"{type(error).__name__}: {error}"
                logger.warning(
                    "%s %s failed (attempt %d): %s", method, url, attempt, last_problem
                )
                continue
            if response.status_code in RETRYABLE_STATUSES:
                last_problem = f"HTTP {response.status_code}"
                logger.warning(
                    "%s %s returned %s (attempt %d)", method, url, last_problem, attempt
                )
                continue
            if raise_on_status:
                _raise_for_status(response)
            return response
        raise FetchFailed(
            f"{method} {url} failed after {MAX_ATTEMPTS} attempts ({last_problem})"
        )

    def _wait_for_domain(self, domain: str) -> None:
        """Sleep until at least min_delay has passed since our last request to this domain."""
        last = self._last_request_at.get(domain)
        if last is not None:
            remaining = self.min_delay - (time.monotonic() - last)
            if remaining > 0:
                time.sleep(remaining)
        self._last_request_at[domain] = time.monotonic()

    def _cache(
        self, firm_id: str, method: str, url: str, response: httpx.Response
    ) -> None:
        """Save the raw body under data/raw/<run_id>/<firm_id>/ and log it in _requests.jsonl."""
        folder = self.cache_root / str(self.run_id) / firm_id
        folder.mkdir(parents=True, exist_ok=True)
        number = self._cache_counter.get(firm_id, 0) + 1
        self._cache_counter[firm_id] = number
        filename = f"{number:03d}_{_slug(url)}{_extension(response)}"
        (folder / filename).write_bytes(response.content)
        entry = {
            "file": filename,
            "method": method,
            "url": _redact_keys(url),
            "status": response.status_code,
            "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        with (folder / "_requests.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps(entry) + "\n")


def _raise_for_status(response: httpx.Response) -> None:
    """Turn a non-2xx response into the right Orbit error."""
    if response.status_code in (401, 403):
        raise FetchBlocked(
            f"{response.request.url} refused us (HTTP {response.status_code})"
        )
    if not response.is_success:
        raise FetchFailed(
            f"{response.request.url} returned HTTP {response.status_code}"
        )


def _slug(url: str) -> str:
    """A short, filesystem-safe name for a URL, so cached files are recognisable."""
    parts = urlsplit(url)
    text = re.sub(r"[^A-Za-z0-9]+", "-", parts.netloc + parts.path).strip("-")
    return text[:80] or "root"


def _extension(response: httpx.Response) -> str:
    """Pick a file extension from the response's content type."""
    content_type = response.headers.get("content-type", "")
    if "json" in content_type:
        return ".json"
    if "html" in content_type:
        return ".html"
    return ".txt"


def _redact_keys(url: str) -> str:
    """Hide API keys in logged URLs (e.g. Davis Polk's Algolia key) so they never land on disk."""
    parts = urlsplit(url)
    query = [
        (name, "REDACTED" if "key" in name.lower() else value)
        for name, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urlunsplit(parts._replace(query=urlencode(query)))
