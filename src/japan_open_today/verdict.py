"""施設・航路の「その日行けるか」を出す（ADR 0004）。判定そのものは sitemill が計算する。

ここがやるのは、レコードに入っている規則を sitemill の `resolve_day` に渡すことと、
鮮度（`hours_fetched_at` / `notices_fetched_at`）を渡すこと。
判定のしかたを決め直したいときは sitemill の ADR 0018 を見る。
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from sitemill.clock import to_jst
from sitemill.jpcal import HolidayCalendar
from sitemill.openstatus import DayVerdict, resolve_day
from sitemill.openstatus.models import DayState, Reason, ReasonCode

from japan_open_today.schema import Route, Spot

# 開館の規則に「第 2・4 水曜日」のような第 n 週の指定がある。曜日の選び方（DaySelector）では
# 表せず、読み取りは「毎週水曜」に落ちる。「第 3 月曜休館」のような休みの指定は休館日の規則で
# 表せるので当てない
_NTH_WEEK_OPEN = re.compile(
    r"第\s*[0-9０-９一二三四五](?:\s*[・、,]\s*[0-9０-９一二三四五])*\s*[月火水木金土日]曜日?"
    # 「曜日」の「日」を省いた一致で後ろを読み違えないよう、先読み側でも「日」を許す
    r"(?!日?\s*(?:は|が)?\s*(?:定休|休))"
)


def hours_unrepresentable(spot: Spot) -> bool:
    """開館の規則が、原文の意味を失った形でしか持てていないか。

    鬼無植木盆栽センターは原文が「9月から6月までの第2・4水曜日」で、データは毎週水曜だった。
    そのまま判定すると第 3・第 5 水曜日も「開館」と断定する（2026-09-17 に確認）。
    """
    return any(
        _NTH_WEEK_OPEN.search(p.evidence.quote)
        for p in spot.hours
        if p.evidence and p.evidence.quote
    )


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
    if hours_unrepresentable(spot):
        # 表せる形に丸めた規則で判定すると、開いていない日を「開館」と断定する。不明にする
        return DayVerdict(
            day=day, state=DayState.unknown, reasons=[Reason(code=ReasonCode.rule_unsupported)]
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
