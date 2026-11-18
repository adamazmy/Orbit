"""Tests for the firm health check (pure logic, no network)."""

from orbit.health import check_health


def test_first_run_with_partners_is_ok():
    assert check_health(65, None) == ("OK", "")


def test_zero_partners_is_suspect_even_on_first_run():
    status, message = check_health(0, None)
    assert status == "SUSPECT"
    assert "no partners" in message


def test_zero_partners_after_ok_run_is_suspect():
    assert check_health(0, 92)[0] == "SUSPECT"


def test_big_drop_is_suspect():
    status, message = check_health(10, 90)
    assert status == "SUSPECT"
    assert "90 -> 10" in message


def test_drop_of_exactly_30_percent_is_ok():
    assert check_health(70, 100)[0] == "OK"


def test_drop_just_over_30_percent_is_suspect():
    assert check_health(69, 100)[0] == "SUSPECT"


def test_growth_is_ok():
    assert check_health(120, 100)[0] == "OK"
