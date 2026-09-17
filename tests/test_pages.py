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


def test_a_spot_without_a_photo_leaves_no_empty_frame(ws: Workspace) -> None:
    """写真が無くても成立するデザインを保つ（ADR 0006）。

    221 施設に写真が無い。空の枠や「写真なし」の板が出ていないことを、生成物で確かめる。
    近くの施設のカードや地図には画像が出るので、**その施設自身の領域**（見出し〜事実の節）
    だけを見る。
    """
    import re

    from japan_open_today.pages import _assets

    dist = ROOT / "dist"
    if not (dist / "index.html").is_file():
        pytest.skip("先に build が要る")
    have = set(_assets(ws))
    checked = 0
    for spot in Dataset.load(ws).spots:
        if spot.spot_id in have:
            continue
        path = dist / spot.path() / "index.html"
        if not path.is_file():
            continue
        body = path.read_text(encoding="utf-8").split("<main", 1)[1].split("</main>")[0]
        own = body.split("</h1>", 1)[1].split('class="facts"', 1)[0]
        checked += 1
        assert "sm-photo" not in own and "<img" not in own, spot.spot_id
        assert not re.search(r"写真(なし|はありません)", own), spot.spot_id
        assert not re.search(r"<(div|section|figure)[^>]*>\s*</(div|section|figure)>", own), (
            spot.spot_id
        )
    assert checked > 100  # 点検が空回りしていないこと


# --- 通常の営業時間と、同じエリアの施設（日付で変わらない本文） ---------------


def _spot_page(built: list, path: str):
    return next(p for p in built if p.meta.path == path)


def test_regular_hours_are_shown_as_facts_with_the_original_wording(built: list) -> None:
    """今日の判定と週の帯は日付で変わる。規則そのものは事実として別に出す。"""
    for prefix, season in (("", "3月21日〜10月20日"), ("en/", "21 Mar – 20 Oct")):
        page = _spot_page(built, f"{prefix}spots/shodoshima/kankakei/index.html")
        hours = page.context["hours"]
        assert hours["lines"][0]["condition"] == season
        assert hours["lines"][0]["time"] == "08:30–17:00"
        assert hours["quotes"] and "8:30~17:00" in hours["quotes"][0]


def test_hours_that_cannot_be_broken_down_show_only_the_original(built: list) -> None:
    """構造化データからは何の時間か読み取れないものは、行にせず原文だけを出す。"""
    for spot in ("kotohira/konpira", "takamatsu/ritsurin"):
        pages = [p for p in built if p.meta.path.endswith(f"spots/{spot}/index.html")]
        assert pages, spot
        for page in pages:
            assert page.context["hours"]["lines"] == [], page.meta.path
            assert page.context["hours"]["quotes"], page.meta.path


def test_an_nth_week_rule_is_not_flattened_into_every_week(ws: Workspace) -> None:
    """「第2・4水曜日」は曜日の選び方で表せず、データでは「毎週水曜」になっている。行にしない。"""
    spot = next(s for s in Dataset.load(ws).spots if s.spot_id == "kinashi-bonsai")
    assert not page_builder._hours_structurable(spot)


def test_a_season_ending_at_month_end_is_not_shown_as_the_31st(ws: Workspace) -> None:
    """「〜6月」は 6 月 31 日として読まれている。表示は「6月末」にする。"""
    from datetime import time

    from sitemill.models.schedule import AnnualSpan, DaySelector, Evidence, HoursPeriod, TimeRange

    ds = Dataset.load(ws)
    base = next(s for s in ds.spots if s.hours)
    spot = base.model_copy(
        update={
            "hours": [
                HoursPeriod(
                    ranges=[TimeRange(start=time(9), end=time(17))],
                    days=DaySelector(),
                    season=AnnualSpan(start_month=4, start_day=1, end_month=6, end_day=31),
                    evidence=Evidence(quote="4月~6月 9:00~17:00"),
                )
            ]
        }
    )
    words = page_builder._wording(ws)
    locales = {loc.code: loc for loc in ws.site.locale_list}
    ja = page_builder._hours_display(spot, words, locales["ja"])
    en = page_builder._hours_display(spot, words, locales["en"])
    assert ja["lines"][0]["condition"] == "4月1日〜6月末"
    assert en["lines"][0]["condition"] == "1 Apr – end of Jun"


def test_every_spot_links_to_other_places_in_its_area(ws: Workspace, built: list) -> None:
    """今日の判定に左右されない、同じエリアへの固定のリンク。クロールの経路にする。"""
    ds = Dataset.load(ws)
    for page in built:
        if "/spots/" not in f"/{page.meta.path}" or page.template != "spot.html":
            continue
        spot = page.context["spot"]
        links = page.context["area_links"]
        others = len(ds.spots_in(spot.area)) - 1
        assert len(links) == min(others, page_builder.AREA_LINKS), page.meta.path
        assert all(spot.path() not in link["url"] for link in links), page.meta.path


def test_area_links_do_not_change_with_the_date(ws: Workspace) -> None:
    """日によって変わると、lastmod を毎日進めてしまう（sitemill ADR 0025）。"""
    ds = Dataset.load(ws)
    one = page_builder.build_pages(ws, ds, now=NOW)
    two = page_builder.build_pages(ws, ds, now=datetime(2026, 9, 20, 21, 10, tzinfo=UTC))
    links_one = {p.meta.path: p.context.get("area_links") for p in one if p.template == "spot.html"}
    links_two = {p.meta.path: p.context.get("area_links") for p in two if p.template == "spot.html"}
    assert links_one == links_two


def test_an_nth_week_opening_rule_is_not_judged_as_every_week(ws: Workspace) -> None:
    """原文「第2・4水曜日」がデータでは毎週水曜。第 3 水曜を「開館」と断定しない。"""
    from datetime import date

    from sitemill.jpcal import HolidayCalendar

    spot = next(s for s in Dataset.load(ws).spots if s.spot_id == "kinashi-bonsai")
    holidays = HolidayCalendar.load(ws.data_dir / "reference" / "syukujitsu.csv")
    verdict = spot_verdict(spot, date(2026, 9, 16), holidays=holidays, stale_after_days=10**6)
    assert verdict.state is DayState.unknown
    assert verdict.has(ReasonCode.rule_unsupported)


def test_an_nth_week_closing_rule_is_left_to_the_closure_rules() -> None:
    """「第3月曜休館」は休館日の規則で表せる。開館の規則の歯止めには当てない。"""
    from japan_open_today.verdict import _NTH_WEEK_OPEN

    assert _NTH_WEEK_OPEN.search("第2・4水曜日 8:00頃~16:00頃")
    assert not _NTH_WEEK_OPEN.search("9:00~17:00（第3月曜休館）")
    assert not _NTH_WEEK_OPEN.search("毎月第1日曜日は休み")
