"""施設・航路の「その日行けるか」を出す（ADR 0004）。判定そのものは sitemill が計算する。

ここがやるのは、レコードに入っている規則を sitemill の `resolve_day` に渡すことと、
鮮度（`hours_fetched_at` / `notices_fetched_at`）を渡すこと。
判定のしかたを決め直したいときは sitemill の ADR 0018 を見る。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sitemill.clock import to_jst
from sitemill.jpcal import HolidayCalendar
from sitemill.openstatus import DayVerdict, resolve_day
from sitemill.openstatus.models import DayState, Reason, ReasonCode

from japan_open_today.schema import Route, Spot


def _stamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return to_jst(datetime.fromisoformat(value))
    except ValueError:
        return None


def _freshness(spot: Spot) -> datetime | None:
    """鮮度の基準。開館時間と告知のうち**古いほう**を採る。

    告知だけ新しくても、開館時間の取得が途切れていれば規則の前提が崩れている。
    """
    stamps = [s for s in (_stamp(spot.hours_fetched_at), _stamp(spot.notices_fetched_at)) if s]
    return min(stamps) if stamps else None


def spot_verdict(
    spot: Spot,
    day: date,
    *,
    holidays: HolidayCalendar | None = None,
    now: datetime | None = None,
    stale_after_days: int | None = None,
) -> DayVerdict:
    if not spot.has_schedule:
        return DayVerdict(
            day=day,
            state=DayState.unknown,
            # detail は空にする。文言はロケールのカタログ（reason.no_data）が持っていて、
            # 同じ文を detail にも入れると画面に二重に出る
            reasons=[Reason(code=ReasonCode.no_data)],
        )
    return resolve_day(
        day,
        hours=spot.hours,
        closures=spot.closures,
        notices=spot.notices,
        holidays=holidays,
        fetched_at=_freshness(spot),
        now=now,
        stale_after_days=stale_after_days,
    )


def spot_week(
    spot: Spot,
    start: date,
    *,
    days: int = 7,
    holidays: HolidayCalendar | None = None,
    now: datetime | None = None,
    stale_after_days: int | None = None,
) -> list[DayVerdict]:
    """ページに出す 7 日分の帯（ADR 0004）。"""
    return [
        spot_verdict(
            spot,
            start + timedelta(days=i),
            holidays=holidays,
            now=now,
            stale_after_days=stale_after_days,
        )
        for i in range(days)
    ]


def route_verdict(
    route: Route,
    day: date,
    *,
    holidays: HolidayCalendar | None = None,
    now: datetime | None = None,
    stale_after_days: int | None = None,
) -> DayVerdict:
    """航路・路線の運航状況。運行日（ダイヤ）と運休告知から決める。"""
    if route.service_days is None and not route.notices:
        return DayVerdict(
            day=day,
            state=DayState.unknown,
            reasons=[Reason(code=ReasonCode.no_data)],
        )
    return resolve_day(
        day,
        notices=route.notices,
        service_days=route.service_days,
        holidays=holidays,
        fetched_at=_stamp(route.notices_fetched_at),
        now=now,
        stale_after_days=stale_after_days,
    )
