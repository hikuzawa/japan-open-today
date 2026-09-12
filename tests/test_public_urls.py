"""公開前の URL 検査（.github/scripts/check_public_urls.py）を固定する。

このサイトは 3 言語で、hreflang が 1 言語でも欠けたり旧ホストを指したりすると、
検索側に別のサイトとして扱われる。配置の直前に必ず通る検査なので、
「何を落とすか」をテストで固定しておく。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / ".github" / "scripts" / "check_public_urls.py"

SITE_TOML = """
[site]
base_url = "https://japan-open-today.pages.dev"

[[locales]]
code = "ja"
path = ""
default = true

[[locales]]
code = "en"
path = "en"

[[locales]]
code = "zh-Hant"
path = "zh-hant"
"""

BASE = "https://japan-open-today.pages.dev"


def _module():
    spec = importlib.util.spec_from_file_location("check_public_urls", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _page(*, alternates: list[tuple[str, str]], canonical: str = f"{BASE}/") -> str:
    links = "\n".join(
        f'<link rel="alternate" hreflang="{lang}" href="{href}">' for lang, href in alternates
    )
    return (
        "<!doctype html><html><head>"
        f'<link rel="canonical" href="{canonical}">'
        f'<meta property="og:url" content="{canonical}">'
        f"{links}</head><body>x</body></html>"
    )


def _dist(tmp_path: Path, html: str) -> tuple[Path, Path]:
    site = tmp_path / "site.toml"
    site.write_text(SITE_TOML, encoding="utf-8")
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(html, encoding="utf-8")
    (dist / "sitemap.xml").write_text(
        f"<urlset><url><loc>{BASE}/</loc></url></urlset>", encoding="utf-8"
    )
    (dist / "robots.txt").write_text(f"Sitemap: {BASE}/sitemap.xml\n", encoding="utf-8")
    return site, dist


ALL_LANGS = [
    ("ja", f"{BASE}/"),
    ("en", f"{BASE}/en/"),
    ("zh-Hant", f"{BASE}/zh-hant/"),
    ("x-default", f"{BASE}/"),
]


def test_a_complete_page_passes(tmp_path: Path) -> None:
    site, dist = _dist(tmp_path, _page(alternates=ALL_LANGS))
    problems, summary = _module().check(site, dist)
    assert problems == []
    assert "hreflang=1x4" in summary


@pytest.mark.parametrize(
    ("alternates", "expected"),
    [
        ([row for row in ALL_LANGS if row[0] != "zh-Hant"], "hreflang に zh-Hant が無い"),
        ([], "hreflang が 1 つも無い"),
        (
            [("ja", "https://japan-open-today.com/"), *ALL_LANGS[1:]],
            "hreflang=ja が base_url 以外を指す",
        ),
        (
            [*ALL_LANGS[:3], ("x-default", f"{BASE}/en/")],
            "x-default が基準言語（ja）と違う URL を指す",
        ),
    ],
    ids=["missing-locale", "no-hreflang", "old-host", "x-default-mismatch"],
)
def test_broken_hreflang_is_caught(
    tmp_path: Path, alternates: list[tuple[str, str]], expected: str
) -> None:
    site, dist = _dist(tmp_path, _page(alternates=alternates))
    problems, _ = _module().check(site, dist)
    assert any(expected in p for p in problems), problems


def test_the_production_domain_can_be_pinned(tmp_path: Path) -> None:
    """カスタムドメインへ切り替えたあと、旧ホストのまま配置されるのを止める。"""
    site, dist = _dist(tmp_path, _page(alternates=ALL_LANGS))
    problems, _ = _module().check(site, dist, expect_base="https://japan-open-today.com")
    assert any("本番ドメイン" in p for p in problems)
