"""Tests for argparse wiring."""

from __future__ import annotations

import pytest

from cleanup_mac import build_parser


def test_defaults():
    p = build_parser()
    args = p.parse_args([])
    assert args.apply is False
    assert args.interactive is False
    assert args.aggressive is False
    assert args.permanent is False
    assert args.min_age == 7
    assert args.min_size == 1
    assert args.verbose is False
    assert args.quiet is False
    assert args.json is False
    assert args.no_log is False
    assert args.ignore_file is None
    assert args.only is None
    assert args.skip is None


def test_apply_and_interactive_mutually_exclusive():
    p = build_parser()
    with pytest.raises(SystemExit):
        p.parse_args(["--apply", "-i"])


def test_aggressive_short_flag():
    p = build_parser()
    args = p.parse_args(["-a"])
    assert args.aggressive is True


def test_only_list():
    p = build_parser()
    args = p.parse_args(["--only", "leftover,xcode"])
    assert args.only == ["leftover", "xcode"]


def test_invalid_category_rejected():
    p = build_parser()
    with pytest.raises(SystemExit):
        p.parse_args(["--only", "not_a_category"])


def test_version_flag(capsys):
    from cleanup_mac import __version__

    p = build_parser()
    with pytest.raises(SystemExit):
        p.parse_args(["--version"])
    out = capsys.readouterr().out
    assert __version__ in out
