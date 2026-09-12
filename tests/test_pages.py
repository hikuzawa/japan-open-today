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


def test_unknown_pages_offer_nearby_open_spots(built: list) -> None:
    """「不明」だけのページは価値が無い。行き先を変えられる材料を必ず置く（ADR 0003 追記）。"""
    unknown_pages = [
        p
        for p in built
        if p.meta.path.startswith("spots/") and p.context["verdict"].state is DayState.unknown
    ]
    assert unknown_pages, "不明の施設が無いので、この検査が意味を持たない"
    for page in unknown_pages:
        nearby = page.context["nearby"]
        assert nearby, page.meta.path
        # 並べるのは「開いていると分かっている」施設だけ。不明を並べても不明が増えるだけ
        assert all(row["verdict"].state is DayState.open for row in nearby), page.meta.path
        assert all(row["spot"].spot_id != page.context["spot"].spot_id for row in nearby)


def test_all_unknown_weeks_hide_the_strip(built: list) -> None:
    """7 日すべて不明なら帯を出さない（同じ「?」が 7 個並ぶだけで情報がない）。"""
    for page in built:
        if not page.meta.path.startswith("spots/"):
            continue
        week = page.context["week"]
        expected = all(v.state is DayState.unknown for v in week)
        assert page.context["week_all_unknown"] is expected, page.meta.path


def test_known_spots_do_not_need_nearby_but_may_have_it(built: list) -> None:
    """開いている施設のページでも近隣を出してよいが、自分は含めない。"""
    for page in built:
        for row in page.context.get("nearby") or []:
            assert row["spot"].spot_id != page.context["spot"].spot_id


def test_open_air_places_say_that_no_hours_are_stated(built: list) -> None:
    """砂浜や境内を「不明」と出すのは事実に合わない。定めが無いことをそのまま出す（ADR 0011）。"""
    pages = [p for p in built if p.meta.path.startswith("spots/")]
    open_air = [p for p in pages if p.context["spot"].spot_type == "open_air"]
    assert open_air, "屋外の場所が無いので、この検査が意味を持たない"
    for page in open_air:
        spot = page.context["spot"]
        if spot.hours or spot.closures or spot.notices:
            continue
        assert page.context["no_hours_stated"] is True, page.meta.path
    # ゲートのある施設ではこの表示をしない（時間が取れていないだけなので「不明」が正しい）
    for page in pages:
        if page.context["spot"].spot_type == "gated":
            assert page.context["no_hours_stated"] is False, page.meta.path


def test_a_place_with_a_notice_is_not_shown_as_having_no_hours(ws: Workspace) -> None:
    """告知が出ているなら、まずその告知を出す。「定めが無い」で上書きしない。"""
    from datetime import date

    from sitemill.models.schedule import DateSpan, NoticeKind, SpecialNotice

    ds = Dataset.load(ws)
    spot = next(s for s in ds.spots if s.spot_type == "open_air")
    assert page_builder.no_hours_stated(spot) is True
    with_notice = spot.model_copy(
        update={
            "notices": [
                SpecialNotice(
                    kind=NoticeKind.closed,
                    span=DateSpan(start=date(2026, 9, 12), end=date(2026, 9, 13)),
                )
            ]
        }
    )
    assert page_builder.no_hours_stated(with_notice) is False


def test_always_open_is_shown_as_a_fact_not_as_missing(built: list) -> None:
    """「年中無休」は定休日が無いと分かっている状態。空欄と同じ扱いにしない。"""
    pages = [p for p in built if p.meta.path.startswith("spots/")]
    for page in pages:
        spot = page.context["spot"]
        if spot.closes_never:
            assert page.context["closures_label"] == "closures.always_open", page.meta.path
        else:
            assert page.context["closures_label"] is None, page.meta.path
