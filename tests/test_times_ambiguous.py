"""その日に当てはまる時間帯が重なり合うときは、判定は出しつつ時刻を出さない（2026-09-28）。

栗林公園は月ごとの表が季節を持たずに読まれ、「開いています」の下に 12 個の時間帯が並んでいた。
外部アクセスはしない。
"""

from __future__ import annotations

from datetime import time

from sitemill.models.schedule import TimeRange

from japan_open_today.verdict import times_ambiguous


def r(a: tuple[int, int], b: tuple[int, int]) -> TimeRange:
    return TimeRange(start=time(*a), end=time(*b))


def test_overlapping_alternatives_are_ambiguous() -> None:
    assert times_ambiguous([r((7, 0), (17, 0)), r((7, 0), (17, 30)), r((5, 30), (18, 30))])


def test_a_lunch_break_split_is_not() -> None:
    assert not times_ambiguous([r((10, 0), (12, 0)), r((13, 0), (17, 0))])


def test_one_range_or_a_repeated_range_is_not() -> None:
    assert not times_ambiguous([r((9, 0), (17, 0))])
    assert not times_ambiguous([r((9, 0), (17, 0)), r((9, 0), (17, 0))])
    assert not times_ambiguous([])
