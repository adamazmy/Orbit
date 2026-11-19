"""Fetcher URL building: no network."""

from orbit.fetch import build_url


def test_url_without_params_is_used_exactly():
    url = "https://example.com/_site/search?v=attorney&p=305&p=306&html"
    assert build_url(url) == url
    assert build_url(url, {}) == url


def test_params_are_added_to_an_existing_query():
    url = build_url("https://example.com/search?v=attorney", {"f": 25, "s": 100})
    assert url == "https://example.com/search?v=attorney&f=25&s=100"


def test_list_params_repeat_the_key():
    url = build_url("https://example.com/search", {"p": ["305", "306"]})
    assert url == "https://example.com/search?p=305&p=306"
