"""運営主体の欄と根拠の引用が意味を成しているかを確かめる（ADR 0009 追記、2026-09-26）。

同じ形の失敗が 3 回あった。引用の断片（「株式会社」が設立した」「Copyright © 2020 Kagawa
Prefectural Gove」）がそのまま運営者の欄に入り、運営者の名前を含まない引用のまま
`facility_official`（施設の公式）として巡回されていた（観光協会 10 件、自治体 24 件、2026-09-26 の
42 件。うち施設の公式 21 件）。書き込む道具を直しても、別の道具や手編集で同じものが戻る。

ここでは 2 つだけを見る。どちらも取り込みの道具（`tools/seed_spots.py`）と、情報源の全件を読む
テスト（CI の checks）の両方から呼ぶ。

1. **運営者の欄が名前であること**。文の一部・著作権表記・ページ題名の断片を入れない
2. **`facility_official` は、根拠の引用に運営者の名前が出てくること**。名前の無い引用で
   「施設の公式」と読まない。引用が英字などで名乗っているとき（「(C) KOTOHIRA-GU」）は、
   引用の中の表記を `operator_evidence.name_in_quote` に書き、その表記が引用にあることを確かめる。
   法人名がサイトに無く、サイトが施設自身のものだと確かめた場合だけ、`operator_note` にそう書いて
   例外にする（道の駅ことひき など）
"""

from __future__ import annotations

import re
from typing import Any

from sitemill.diff.normalize import squash

# 名前ではなく、文・表記・題名の断片であることを示す書き方
FRAGMENT = re.compile(
    r"[「」『』]"  # 引用符の片割れ
    r"|ました|した$|しております|しています|います$|に関して|により|による|をお招き|に移行"
    r"|詳細へ|概要$|沿革$|所在地$"  # 見出し・リンクの文字列
    r"|Copyright|©|\(C\)|All Rights|Reserved"  # 著作権表記
    r"|[｜|]| - |×"  # ページ題名の区切り、共催の表記
    r"|\d{4}\.\d{1,2}\.\d{1,2}"  # 日付
    r"|^(?:公益財団法人|一般財団法人|公益社団法人|一般社団法人|株式会社|有限会社|合同会社)$",
    re.I,
)
# 1 つの欄に複数の運営者を並べるときの区切り
# （「株式会社ベネッセコーポレーション・公益財団法人福武財団」）
JOINERS = re.compile(r"・|、|／|/")


def operator_problems(entry: dict[str, Any]) -> list[str]:
    """情報源 1 件の運営者の欄と引用の問題。無ければ空。"""
    operator = " ".join(str(entry.get("operator") or "").split())
    kind = str(entry.get("operator_kind") or "")
    evidence = entry.get("operator_evidence") or {}
    quote = str(evidence.get("quote") or "")
    spelled = str(evidence.get("name_in_quote") or "")
    problems: list[str] = []
    if operator and (FRAGMENT.search(operator) or len(operator) > 40):
        problems.append(f"運営者の欄が名前でない（引用の断片）: {operator[:40]}")
    if spelled and squash(spelled) not in squash(quote):
        problems.append(f"name_in_quote の表記が引用に無い: {spelled[:40]}")
    if kind == "facility_official":
        if not operator:
            problems.append("施設の公式なのに運営者の名前が無い")
        elif not (entry.get("operator_note") or spelled or quote_names(quote, operator)):
            problems.append(f"施設の公式なのに、根拠の引用に運営者の名前が無い: {operator[:40]}")
    return problems


def quote_names(quote: str, operator: str) -> bool:
    """引用が運営者の名前を含むか。空白の有無は問わない。複数の運営者はそれぞれを探す。"""
    text = squash(quote)
    parts = [squash(p) for p in JOINERS.split(operator) if p.strip()]
    return bool(parts) and all(p and p in text for p in parts)
