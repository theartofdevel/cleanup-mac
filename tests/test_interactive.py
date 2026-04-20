"""Tests for interactive confirmation prompt."""

from __future__ import annotations

from io import StringIO
from pathlib import Path

from cleanup_mac import Candidate, prompt_confirm_category


def _c(name: str) -> Candidate:
    return Candidate(
        path=Path(f"/tmp/{name}"), size_bytes=1024, category="user_cache", reason="t"
    )


def test_yes_returns_true():
    out = StringIO()
    assert (
        prompt_confirm_category(
            category="user_cache",
            items=[_c("a")],
            in_stream=StringIO("y\n"),
            out_stream=out,
        )
        is True
    )


def test_default_no_returns_false():
    out = StringIO()
    assert (
        prompt_confirm_category(
            category="user_cache",
            items=[_c("a")],
            in_stream=StringIO("\n"),  # just <Enter>
            out_stream=out,
        )
        is False
    )


def test_details_then_yes():
    """'d' prints details, then reprompts. Subsequent 'y' accepts."""
    out = StringIO()
    assert (
        prompt_confirm_category(
            category="user_cache",
            items=[_c("a"), _c("b")],
            in_stream=StringIO("d\ny\n"),
            out_stream=out,
        )
        is True
    )
    output = out.getvalue()
    assert "/tmp/a" in output
    assert "/tmp/b" in output


def test_quit_raises():
    import pytest

    from cleanup_mac import UserQuit

    with pytest.raises(UserQuit):
        prompt_confirm_category(
            category="user_cache",
            items=[_c("a")],
            in_stream=StringIO("q\n"),
            out_stream=StringIO(),
        )
