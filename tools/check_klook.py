"""Klook の飛び先を人が確かめるための一覧（ADR 0012 追記、2026-09-23）。

商品は消えることがあり、Klook のページ構成が変わると目的地や検索結果の URL も切れる。
切れたまま広告を出すと、利用者は Klook の「ページが見つかりません」に着く。

**ここからは Klook に一切アクセスしない。** 掲載規約 4.3(a) が「KLOOK Booking Platform の
いかなる部分も、走査したり機械的に評価・抽出したりしない」と定めているため、サイトマップの
自動点検（2026-09-22 に作ったもの）は取りやめた。robots.txt は `Sitemap:` を公開しているが、
規約のほうが狭いので規約に合わせる。検索結果は robots.txt でも禁じられている。

代わりに、**週次まとめに飛び先の一覧を出して、人がブラウザで開いて確かめる**。
確かめること: ページが開く／香川の商品が並ぶ／施設の商品は施設の名前が商品名に出ている。

使い方: uv run python -m tools.check_klook
"""

from __future__ import annotations

from japan_open_today import klook


def main() -> int:
    lines = [
        "## Klook の飛び先（人が開いて確かめる）",
        "",
        "掲載規約 4.3(a)（走査・機械的な抽出の禁止）により、こちらからは自動で開かない。"
        "月に 1 度でよいので、下の URL をブラウザで開いて、ページが生きているかを見る。",
        "",
        "| 飛び先 | 何を確かめるか | URL（日本語） |",
        "|---|---|---|",
    ]
    for landing in klook.landings():
        if landing.spots:
            what = f"商品名に「{'・'.join(landing.spots)}」の施設名が出ているか"
        elif landing.areas:
            what = "そのエリアの商品が並んでいるか"
        else:
            what = "香川の商品が並んでいるか"
        lines.append(f"| `{landing.id}` | {what} | {landing.urls.get('ja', '（未設定）')} |")
    lines += [
        "",
        f"- 対応表は `src/japan_open_today/klook.py`（提携 ID {klook.AID}、"
        f"URL を辿った日 {klook.CHECKED_ON}）",
        "- 切れていたら、その飛び先を対応表から外す（施設は 1 つ上の段の飛び先に落ちる）",
    ]
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
