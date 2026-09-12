"""ページ生成の検査（ADR 0003・0004・0005）。実データの dist は作らず、ページの組み立てだけ見る。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sitemill.openstatus import DayState, ReasonCode
from sitemill.settings import Workspace

from japan_open_today import pages as page_builder
from japan_open_today.data import Dataset
from japan_open_today.verdict import spot_verdict, spot_week

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 12, 21, 10, tzinfo=UTC)  # JST では 9/13 06:10


@pytest.fixture(scope="module")
def ws() -> Workspace:
    return Workspace.open(ROOT)


@pytest.fixture(scope="module")
def built(ws: Workspace) -> list:
    return page_builder.build_pages(ws, Dataset.load(ws), now=NOW)


def test_every_page_declares_its_locale_and_language_versions(built: list) -> None:
    """多言語サイトは hreflang が相互に揃っていないと検索エンジンに無視される。"""
    for page in built:
        assert page.meta.locale, page.meta.path
        if page.meta.noindex:
            continue
        assert page.meta.alternates, page.meta.path
        assert page.meta.alternates[page.meta.locale] == page.meta.url_path, page.meta.path


def test_pages_exist_for_all_three_locales(ws: Workspace, built: list) -> None:
    paths = {p.meta.path for p in built}
    assert "index.html" in paths
    assert "en/index.html" in paths
    assert "zh-hant/index.html" in paths
    assert "spots/naoshima/chichu/index.html" in paths
    assert "en/spots/naoshima/chichu/index.html" in paths
    assert "404.html" in paths


def test_every_page_has_trust_signals(built: list) -> None:
    for page in built:
        assert page.trust.updated_at is not None, page.meta.path
        assert page.trust.operator.name, page.meta.path


def test_the_day_is_taken_in_jst(ws: Workspace, built: list) -> None:
    """UTC の 9/12 21:10 は JST では 9/13。判定の基準日がずれていないことを確かめる。"""
    spot_page = next(p for p in built if p.meta.path.startswith("spots/"))
    assert spot_page.context["today"].isoformat() == "2026-09-13"
    assert spot_page.context["verdict"].day.isoformat() == "2026-09-13"


def test_week_strip_has_seven_dated_days(built: list) -> None:
    """「今日」だけの表記は使わず、日付を明記した 7 日分を出す（ADR 0004）。"""
    spot_page = next(p for p in built if p.meta.path.startswith("spots/"))
    week = spot_page.context["week"]
    assert len(week) == 7
    assert [v.day.isoformat() for v in week][0] == "2026-09-13"
    assert len({v.day for v in week}) == 7


def test_unknown_verdicts_always_carry_a_reason(ws: Workspace) -> None:
    """不明と言うときは理由を必ず出す。理由の無い「不明」は利用者に何も渡さない。"""
    ds = Dataset.load(ws)
    for spot in ds.spots:
        verdict = spot_verdict(spot, page_builder.jst_today(NOW))
        if verdict.state is DayState.unknown:
            assert verdict.reasons, spot.spot_id
            assert verdict.reasons[0].code is not ReasonCode.regular_hours, spot.spot_id


def test_open_verdicts_have_hours_or_an_always_open_statement(ws: Workspace) -> None:
    """「開いている」と言うなら時間帯か「時間の指定なし」の根拠がある（推測で開けない）。"""
    ds = Dataset.load(ws)
    for spot in ds.spots:
        verdict = spot_verdict(spot, page_builder.jst_today(NOW))
        if verdict.state is DayState.open:
            assert verdict.periods or verdict.has(ReasonCode.always_open), spot.spot_id


def test_search_index_is_split_per_locale(ws: Workspace) -> None:
    index = page_builder.search_index(ws, Dataset.load(ws), now=NOW)
    assert set(index) == {"ja.json", "en.json", "zh-Hant.json"}
    rows = index["en.json"]["spots"]
    assert rows and all(r["url"].startswith("/en/") for r in rows)
    assert all(r["state"] in ("open", "closed", "unknown") for r in rows)


def test_only_usable_assets_reach_the_pages(ws: Workspace, built: list) -> None:
    """ページに出せるのはライセンス判定を通った資産だけ（sitemill ADR 0020）。"""
    for page in built:
        photo = page.context.get("photo")
        if photo is not None:
            assert photo.usable, page.meta.path
            assert photo.page_url, page.meta.path


def test_weeks_differ_between_spots_with_and_without_rules(ws: Workspace) -> None:
    """規則がある施設は日によって変わり、無い施設は 7 日すべて不明になる。"""
    ds = Dataset.load(ws)
    with_rules = [s for s in ds.spots if s.closures]
    without = [s for s in ds.spots if not s.has_schedule]
    assert with_rules and without
    week = spot_week(with_rules[0], page_builder.jst_today(NOW))
    assert len({v.state for v in week}) > 1
    week = spot_week(without[0], page_builder.jst_today(NOW))
    assert {v.state for v in week} == {DayState.unknown}
