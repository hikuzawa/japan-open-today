"""事実の鮮度は、出どころのページを最後に読めた時刻まで進める（2026-09-28）。外部アクセスはしない。

事実の `*_fetched_at` は抽出した時刻で、変わっていないページは抽出し直さないので進まない。
そのため最初の抽出から 14 日たつと、毎日読めている施設まで「取得が 14 日以上できていない」に落ちた
（9/28 の月曜に不明 152 件）。
"""

from __future__ import annotations

from datetime import UTC, datetime

from japan_open_today.data import _refreshed

EXTRACTED = "2026-09-12T10:39:17+00:00"
HOURS = "https://a.example/hours"
NEWS = "https://a.example/news"
READ = {
    HOURS: datetime(2026, 9, 27, 22, 46, tzinfo=UTC),
    NEWS: datetime(2026, 9, 25, 22, 0, tzinfo=UTC),
}


def test_a_page_read_again_unchanged_keeps_its_facts_fresh() -> None:
    assert _refreshed(EXTRACTED, {HOURS}, READ) == "2026-09-27T22:46:00+00:00"


def test_the_oldest_read_of_several_pages_decides() -> None:
    assert _refreshed(EXTRACTED, {HOURS, NEWS}, READ) == "2026-09-25T22:00:00+00:00"


def test_a_page_never_read_holds_the_extraction_time() -> None:
    """1 枚でも読めた記録が無ければ（取得の失敗・巡回先から外した URL）、進めない。"""
    assert _refreshed(EXTRACTED, {HOURS, "https://a.example/gone"}, READ) == EXTRACTED
    assert _refreshed(EXTRACTED, set(), READ) == EXTRACTED


def test_it_never_moves_backwards() -> None:
    later = "2026-09-28T01:00:00+00:00"
    assert _refreshed(later, {HOURS}, READ) == later


def test_facts_without_a_timestamp_take_the_read_time() -> None:
    assert _refreshed(None, {NEWS}, READ) == "2026-09-25T22:00:00+00:00"
