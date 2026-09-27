"""運営主体の欄と根拠の引用が意味を成しているかを確かめる（ADR 0009 追記、2026-09-26・27）。

同じ形の失敗が 3 回あった。引用の断片（「株式会社」が設立した」「Copyright © 2020 Kagawa
Prefectural Gove」）がそのまま運営者の欄に入り、運営者の名前を含まない引用のまま
`facility_official`（施設の公式）として巡回されていた（観光協会 10 件、自治体 24 件、2026-09-26 の
42 件。うち施設の公式 21 件）。書き込む道具を直しても、別の道具や手編集で同じものが戻る。

取り込みの道具（`tools/seed_spots.py`）と、情報源の全件を読むテスト（CI の checks）の両方から呼ぶ。

1. **運営者の欄が名前であること**。文の一部・著作権表記・ページ題名の断片を入れない
2. **`facility_official` は、根拠の引用に運営者の名前が出てくること**。名前の無い引用で
   「施設の公式」と読まない。引用が英字などで名乗っているとき（「(C) KOTOHIRA-GU」）は、
   引用の中の表記を `operator_evidence.name_in_quote` に書き、その表記が引用にあることを確かめる。
   法人名がサイトに無く、サイトが施設自身のものだと確かめた場合だけ、`operator_note` にそう書いて
   例外にする（道の駅ことひき など）

種別をホストで決める情報源にも、引用が「その運営主体の名乗り」であることを求める（2026-09-27）。
高松市の 4 館は、引用が別の施設（サンポートホール高松）の指定管理者の話だったのに通っていた。

3. **自治体・県の引用は自らの名乗り**（`municipal_self_id`）。著作権表記、役所・役場・教育委員会の
   連絡先（〒・電話つき）、「〜によって管理・運営」。指定管理の施設は「指定管理」の記述でもよい
4. **根拠はその情報源のサイトにある**（`evidence_on_site`）。根拠の URL のホストが巡回先・公式の
   ホストのどれか（`www.` の有無は問わない）
5. **観光協会の引用・欄に協会の名前がある**（`association_named`）。観光協会のページを巡回して
   いるのに、ページに載っている施設の運営会社を運営者にしていた（鎌田共済会・大江戸温泉など）

3〜5 は、理由があって満たせない情報源だけ `operator_check_exceptions: {規則名: 理由}` で外せる。
理由が空の例外、知らない規則名、もう当てはまらない（外さなくても通る）例外は、それ自体を問題にする。
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

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
# 自治体・県の名乗り。フッターの著作権表記か、役所などの連絡先（住所・電話つき）
MUNICIPAL_SELF = re.compile(
    r"Copyright|©|\(C\)"
    r"|(?:役所|役場|県庁|教育委員会|庁).{0,30}(?:〒|電話|TEL)"
    r"|〒.{0,40}(?:役所|役場|県庁)"
    r"|によって管理・運営",
    re.I,
)
ASSOCIATION = re.compile(
    r"観光協会|観光交流局|観光コンベンション|ビューロー|観光連盟|地域経営機構|Tourism Association",
    re.I,
)
RULES = ("municipal_self_id", "evidence_on_site", "association_named")


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
    if evidence:
        problems += _rule_problems(entry, kind, operator, quote, str(evidence.get("url") or ""))
    return problems


def _rule_problems(
    entry: dict[str, Any], kind: str, operator: str, quote: str, url: str
) -> list[str]:
    """規則 3〜5 と、その例外の書き方の問題。"""
    broken: dict[str, str] = {}
    if kind in ("municipality", "prefecture") and not MUNICIPAL_SELF.search(quote):
        broken["municipal_self_id"] = f"自治体・県の引用が自らの名乗りでない: {quote[:40]}"
    if kind == "municipality_affiliated" and not (
        MUNICIPAL_SELF.search(quote) or "指定管理" in quote
    ):
        broken["municipal_self_id"] = f"指定管理の引用に名乗りも指定管理の記述も無い: {quote[:40]}"
    if url and _host(url) not in _site_hosts(entry):
        broken["evidence_on_site"] = f"根拠がこの情報源のサイトに無い: {_host(url)}"
    if kind == "tourism_association" and not (
        ASSOCIATION.search(quote) or ASSOCIATION.search(operator)
    ):
        broken["association_named"] = f"観光協会の引用・欄に協会の名前が無い: {operator[:40]}"

    problems: list[str] = []
    exceptions = entry.get("operator_check_exceptions") or {}
    if not isinstance(exceptions, dict):
        return ["operator_check_exceptions は {規則名: 理由} の形で書く"]
    for rule, reason in exceptions.items():
        if rule not in RULES:
            problems.append(f"知らない規則の例外: {rule}（{', '.join(RULES)} のどれか）")
        elif not str(reason or "").strip():
            problems.append(f"理由の無い例外: {rule}")
        elif rule not in broken:
            problems.append(f"もう当てはまらない例外: {rule}（外しても通る）")
    for rule, message in broken.items():
        if not str(exceptions.get(rule) or "").strip():
            problems.append(message)
    return problems


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def _site_hosts(entry: dict[str, Any]) -> set[str]:
    """その情報源のサイトとみなすホスト（巡回先・許可ホスト・公式 URL）。`www.` は外す。"""
    hosts = {str(h).lower().removeprefix("www.") for h in entry.get("allow_hosts") or []}
    hosts |= {_host(p.get("url") or "") for p in entry.get("pages") or []}
    hosts.add(_host(str(entry.get("official_url") or "")))
    return {h for h in hosts if h}


def quote_names(quote: str, operator: str) -> bool:
    """引用が運営者の名前を含むか。空白の有無は問わない。複数の運営者はそれぞれを探す。"""
    text = squash(quote)
    parts = [squash(p) for p in JOINERS.split(operator) if p.strip()]
    return bool(parts) and all(p and p in text for p in parts)
