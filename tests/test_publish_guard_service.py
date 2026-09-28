"""公開前の歯止めの指標（sitemill ADR 0026）。本番のトップの「不明」と同じ数え方か。

外部アクセスはしない。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sitemill.settings import Workspace

from japan_open_today import pages as page_builder
from japan_open_today.data import Dataset
from japan_open_today.service import service

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 12, 21, 10, tzinfo=UTC)


def test_the_guard_counts_unknown_the_way_the_top_page_does() -> None:
    ws = Workspace.open(ROOT)
    metric = service.publish_metrics(ws, now=NOW)["unknown"]
    top = next(
        p
        for p in page_builder.build_pages(ws, Dataset.load(ws), now=NOW)
        if p.meta.path == "index.html"
    )
    assert metric.count == top.context["counts"]["unknown"]
    assert metric.total == len(Dataset.load(ws).spots)


def test_every_limit_says_why() -> None:
    for limit in service.publish_limits.values():
        assert limit.reason.strip()
        assert limit.max_rise is not None or limit.max_share is not None
