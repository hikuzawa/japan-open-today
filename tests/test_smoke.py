"""足場の検査。site.toml が読めること、サービスの規約が守られていることを確かめる。

サービス実装（service.py）は S4 で入る。ここでは設定と方針の検査だけを行う。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sitemill.settings import SiteConfig

import japan_open_today

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def site() -> SiteConfig:
    return SiteConfig.load(ROOT / "site.toml")


def test_version() -> None:
    assert japan_open_today.__version__


def test_site_config_loads(site: SiteConfig) -> None:
    assert site.id == "japan-open-today"
    assert site.name == "Japan Open Today"
    assert site.service == "japan_open_today.service:service"
    assert site.language == "ja"


def test_base_url_has_no_trailing_slash(site: SiteConfig) -> None:
    """基準 URL はここ 1 か所で差し替える。末尾のスラッシュは付けない（canonical が二重になる）。"""
    assert site.base_url.startswith("https://")
    assert not site.base_url.endswith("/")


def test_user_agent_points_at_the_site(site: SiteConfig) -> None:
    """巡回先から誰が来たのか分かるようにする（sitemill ADR 0003）。"""
    ua = site.user_agent
    assert site.id in ua and site.base_url in ua


def test_crawl_is_polite(site: SiteConfig) -> None:
    """ホストごとに間隔を空け、週 1 回は必ず取りに行く（ADR 0007）。"""
    assert site.crawl.default_delay_seconds >= 1.0
    assert site.crawl.max_interval_days <= 7


def test_operator_is_not_yet_published(site: SiteConfig) -> None:
    """運営者名が決まるまでは「準備中」。決めたらこのテストを実際の値に直す（human-tasks 8）。"""
    assert site.operator.name == "準備中"


def test_env_example_has_no_values() -> None:
    """.env.example には値を書かない（git にコミットされる）。"""
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        key, _, value = line.partition("=")
        assert value == "", f"{key} に値が書かれている"


def test_adrs_are_numbered_without_gaps() -> None:
    """設計判断は ADR に残す。番号の重複と欠番を防ぐ。"""
    numbers = sorted(
        int(p.name[:4]) for p in (ROOT / "docs" / "adr").glob("[0-9][0-9][0-9][0-9]-*.md")
    )
    assert numbers == list(range(1, len(numbers) + 1))
