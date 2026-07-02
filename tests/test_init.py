"""Smoke test for the incendiscat integration scaffold."""

from custom_components.incendiscat.const import DOMAIN


def test_domain() -> None:
    """The integration domain must be 'incendiscat'."""
    assert DOMAIN == "incendiscat"
