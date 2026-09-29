"""事実の鮮度は、出どころのページを最後に読めた時刻まで進める（2026-09-28、2026-09-29）。外部アクセスはしない。

事実の `*_fetched_at` は抽出した時刻で、変わっていないページは抽出し直さないので進まない。
そのため最初の抽出から 14 日たつと、毎日読めている施設まで「取得が 14 日以上できていない」に落ちた
（9/28 の月曜に不明 152 件）。「読めた」の判定は sitemill の `read_at`（提案 L）で、失敗が無く、
抽出したときから中身が変わっていないページだけを数える。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sitemill.diff.state import CrawlState, UrlState

from japan_open_today.data import _refreshed

EXTRACTED = "2026-09-12T10:39:17+00:00"
HOURS = "https://a.example/hours"
NEWS = "https://a.example/news"
READ_HOURS = datetime(2026, 9, 27, 22, 46, tzinfo=UTC)
READ_NEWS = datetime(2026, 9, 25, 22, 0, tzinfo=UTC)


def page(url: str, fetched: datetime, **kw: object) -> UrlState:
    base: dict[str, object] = {
        "url": url,
        "source_id": "s",
        "fetched_at": fetched,
        "content_hash": "h1",
        "extracted_hash": "h1",
    }
    base.update(kw)
    return UrlState.model_validate(base)


def state(*pages: UrlState) -> CrawlState:
    return CrawlState(urls={p.url: p for p in pages})


READ = state(page(HOURS, READ_HOURS), page(NEWS, READ_NEWS))


def test_a_page_read_again_unchanged_keeps_its_facts_fresh() -> None:
    assert _refreshed(EXTRACTED, {HOURS}, READ) == "2026-09-27T22:46:00+00:00"


def test_the_oldest_read_of_several_pages_decides() -> None:
    assert _refreshed(EXTRACTED, {HOURS, NEWS}, READ) == "2026-09-25T22:00:00+00:00"


def test_a_page_never_read_holds_the_extraction_time() -> None:
    """1 枚でも読めた記録が無ければ（取得の失敗・巡回先から外した URL）、進めない。"""
    assert _refreshed(EXTRACTED, {HOURS, "https://a.example/gone"}, READ) == EXTRACTED
    assert _refreshed(EXTRACTED, set(), READ) == EXTRACTED


def test_a_changed_page_not_yet_extracted_does_not_move_it() -> None:
    """中身が変わったのに抽出がまだ（失敗した晩）。古い事実を新しく扱わない（提案 L の条件）。"""
    changed = state(page(HOURS, READ_HOURS, content_hash="h2"))
    assert _refreshed(EXTRACTED, {HOURS}, changed) == EXTRACTED
    failed = state(page(HOURS, READ_HOURS, error="HTTP 500"))
    assert _refreshed(EXTRACTED, {HOURS}, failed) == EXTRACTED


def test_it_never_moves_backwards() -> None:
    later = "2026-09-28T01:00:00+00:00"
    assert _refreshed(later, {HOURS}, READ) == later


def test_facts_without_a_timestamp_take_the_read_time() -> None:
    assert _refreshed(None, {NEWS}, READ) == "2026-09-25T22:00:00+00:00"
