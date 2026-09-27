"""エリアの「となり」の表（2026-09-27）。外部アクセスはしない。"""

from __future__ import annotations

from japan_open_today.areas import BY_SLUG, ISLANDS, NEIGHBOR_PAIRS, neighbors


def test_every_pair_says_why_and_joins_two_mainland_areas() -> None:
    """根拠を言える組だけを入れる。理由の無い組・島を含む組・同じ組の重複は入れない。"""
    seen: set[frozenset[str]] = set()
    for a, b, reason in NEIGHBOR_PAIRS:
        assert a in BY_SLUG and b in BY_SLUG and a != b, (a, b)
        assert a not in ISLANDS and b not in ISLANDS, (a, b)
        assert reason.strip(), (a, b)
        key = frozenset({a, b})
        assert key not in seen, (a, b)
        seen.add(key)


def test_neighbours_read_both_ways_and_islands_have_none() -> None:
    for a, b, _ in NEIGHBOR_PAIRS:
        assert b in neighbors(a) and a in neighbors(b)
    for slug in ISLANDS:
        assert neighbors(slug) == ()
