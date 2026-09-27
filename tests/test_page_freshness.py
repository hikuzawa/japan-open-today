"""公式ページを読めていないあいだは「記載が無い」「告知が出ていない」と言わない（2026-09-27）。

サンポート高松と豊島は、CI から robots.txt が取れず 9/12 から公式ページを読めていなかった。
それでも屋外の場所として「開館時間の定めが記載されていません／告知も出ていません」と出し続けていた。
外部アクセスはしない。
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sitemill.openstatus.models import DayState, ReasonCode

from japan_open_today.pages import no_hours_stated
from japan_open_today.schema import Spot
from japan_open_today.verdict import spot_verdict

NOW = datetime(2026, 9, 27, 3, 0, tzinfo=UTC)  # 12:00 JST
DAY = date(2026, 9, 27)


def open_air(read_at: str | None) -> Spot:
    return Spot.model_validate(
        {
            "spot_id": "teshima-navi",
            "source_id": "kagawa-teshima-navi",
            "spot_type": "open_air",
            "area": "teshima",
            "category": "other",
            "names": {"ja": {"text": "豊島", "source": "ja"}},
            "official_url": "https://teshima-navi.jp/",
            "operator": "NPO法人豊島観光協会",
            "page_fetched_at": read_at,
        }
    )


def test_a_page_read_this_week_still_says_no_hours_are_stated() -> None:
    spot = open_air("2026-09-24T23:00:00+00:00")
    assert no_hours_stated(spot, now=NOW, stale_after_days=14)
    verdict = spot_verdict(spot, DAY, now=NOW, stale_after_days=14)
    assert [r.code for r in verdict.reasons] == [ReasonCode.no_data]


def test_a_page_not_read_for_14_days_falls_back_to_a_stale_unknown() -> None:
    spot = open_air("2026-09-12T23:15:56+00:00")
    assert not no_hours_stated(spot, now=NOW, stale_after_days=14)
    verdict = spot_verdict(spot, DAY, now=NOW, stale_after_days=14)
    assert verdict.state is DayState.unknown
    assert [r.code for r in verdict.reasons] == [ReasonCode.stale_source, ReasonCode.no_data]
    # 最後に読めた日時は、画面の「取得: …」に出るよう evidence に入る
    assert verdict.reasons[0].fetched_at is not None
    assert verdict.reasons[0].fetched_at.date() == date(2026, 9, 13)  # JST
    assert verdict.reasons[0].detail == ""  # 日本語の補足を他言語のページに漏らさない


def test_a_page_never_read_is_not_described_either() -> None:
    assert not no_hours_stated(open_air(None), now=NOW, stale_after_days=14)


def test_without_a_freshness_limit_the_old_behaviour_holds() -> None:
    """鮮度の下限を渡さない呼び出し（道具など）は、これまでどおり。"""
    assert no_hours_stated(open_air(None))
