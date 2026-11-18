"""Health check: decides whether a firm's fetched snapshot can be trusted.

A scraper that silently breaks usually returns far fewer people, not an error.
Without this gate, a broken parser would be reported as dozens of partners
leaving. So a snapshot only counts as OK when:
- it has at least one partner, and
- it hasn't dropped by more than 30% since that firm's last OK run.

Otherwise it's SUSPECT: it's stored for the record but produces no change
events, and future runs keep comparing against the last OK snapshot instead.
FAILED and BLOCKED are decided earlier, from adapter errors.
"""

MAX_DROP = 0.30


def check_health(partner_count: int, last_ok_count: int | None) -> tuple[str, str]:
    """Return (status, message) for a fetched snapshot: ("OK", "") or ("SUSPECT", why).

    last_ok_count is the partner count of this firm's last OK run, or None if
    there's none yet (first run: only the zero check applies).
    """
    if partner_count == 0:
        return "SUSPECT", "no partners found"
    if last_ok_count:
        drop = (last_ok_count - partner_count) / last_ok_count
        if drop > MAX_DROP:
            change = f"{last_ok_count} -> {partner_count}"
            return (
                "SUSPECT",
                f"partners dropped {drop:.0%} ({change}) since last OK run",
            )
    return "OK", ""
