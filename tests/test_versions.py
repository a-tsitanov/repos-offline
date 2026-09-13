import pytest

from offpack.versions import latest_needs_fix, max_stable, stable_key


def test_stable_key():
    assert stable_key("1.10.2") == (1, 10, 2)
    assert stable_key("1.0.0+build.5") == (1, 0, 0)
    assert stable_key("2.0.0-beta.1") is None
    assert stable_key("latest") is None


def test_max_stable():
    assert max_stable(["1.0.0", "1.10.0", "1.9.0"]) == "1.10.0"
    assert max_stable(["2.0.0-beta.1", "1.0.0"]) == "1.0.0"
    assert max_stable(["2.0.0-rc.1"]) is None
    assert max_stable([]) is None


@pytest.mark.parametrize(
    ("current", "versions", "expected"),
    [
        ("1.0.0", ["1.0.0", "2.0.0"], "2.0.0"),
        ("2.0.0", ["1.0.0", "2.0.0"], None),
        (None, ["1.0.0"], "1.0.0"),
        ("3.0.0-rc.1", ["2.0.0", "3.0.0-rc.1"], "2.0.0"),
        ("1.0.0", ["1.0.0+b"], None),
        ("1.0.0", ["0.9.0-beta"], None),
    ],
)
def test_latest_needs_fix(current, versions, expected):
    assert latest_needs_fix(current, versions) == expected
