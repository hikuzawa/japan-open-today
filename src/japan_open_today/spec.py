"""抽出仕様。LLM は原文の引用だけを返し、値は sitemill の決定的パーサが作る（ADR 0002）。

ページ種別ごとに仕様を分ける:
  spot_detail / spot_hours / spot_fees / spot_access → SPOT_SPEC
  notice                                            → NOTICE_SPEC
  shared_notice                                     → SHARED_NOTICE_SPEC（複数施設の共有ページ）
  timetable / route_fares                           → ROUTE_SPEC
仕様が無い種別は抽出されず、実行レポートで `no_spec` として数えられる。
"""

from __future__ import annotations

import re
from importlib import resources
from typing import Any

from sitemill.clock import jst_today
from sitemill.extract import ExtractionSpec, QuoteField
from sitemill.parse.jp import parse_yen
from sitemill.parse.jp.closures import parse_closures, parse_service_days
from sitemill.parse.jp.dates import parse_date_range
from sitemill.parse.jp.duration import parse_minutes
from sitemill.parse.jp.hours import parse_clock, parse_opening_hours

SPOT_PROMPT_VERSION = "spot_v1"
NOTICE_PROMPT_VERSION = "notice_v1"
SHARED_NOTICE_PROMPT_VERSION = "shared_notice_v1"
ROUTE_PROMPT_VERSION = "route_v1"

# 料金ではない金額。抽出できても値にしない（akiya-atlas の spec と同じ考え方）
_NOT_A_FEE = re.compile(
    r"団体|回数券|年間パスポート|定期|レンタル|駐車|送料|手数料|保証金|寄付|募金|補助|助成"
    # 1 日乗車券・フリー券は「その区間の運賃」ではない。運賃として出すと利用者の計算が狂う。
    # 「往復」はここに入れない。ロープウェイや遊覧船では往復券が通常の料金なので、
    # 除くと本当の料金が出せなくなる（片道だけを出すのは航路側のプロンプトで縛る）
    r"|1日乗車券|一日乗車券|1日券|一日券|フリー(?:乗車)?券"
)
_FREE = re.compile(r"無料|無償|不要|free", re.I)


def parse_fee(quote: str) -> tuple[int | None, str | None]:
    """料金の引用を値にする。団体割引などの金額は値にしない。無料は 0 円として扱う。"""
    text = quote or ""
    if _NOT_A_FEE.search(text):
        return None, "not_an_individual_fee"
    if _FREE.search(text) and not any(ch.isdigit() for ch in text):
        return 0, "free"
    return parse_yen(text)


def parse_time_of_day(quote: str) -> tuple[str | None, str | None]:
    """始発・終発の時刻。`FieldValue[str]` に入れるので "HH:MM" の文字列にする。"""
    value = parse_clock(quote or "")
    return (value.strftime("%H:%M"), None) if value is not None else (None, "no_clock")


def parse_notice_span(quote: str) -> tuple[str | None, str | None]:
    """告知の期間。"YYYY-MM-DD/YYYY-MM-DD" の文字列にして持つ（年は JST の今年を基準）。

    「本日」「明日」のような相対表現は値にしない。生成した日と告知を読んだ日がずれるため、
    相対表現から日付を作ると 1 日単位で嘘になる。
    """
    text = quote or ""
    if re.search(r"本日|当日|明日|あす|来週|今週|現在", text):
        return None, "relative_date"
    span, note = parse_date_range(text, year=jst_today().year)
    if span is None:
        return None, note
    return f"{span[0].isoformat()}/{span[1].isoformat()}", None


def _load(version: str) -> str:
    return (
        resources.files("japan_open_today")
        .joinpath("prompts", f"{version}.md")
        .read_text(encoding="utf-8")
    )


def _nullable(kind: str, description: str) -> dict[str, Any]:
    return {"type": [kind, "null"], "description": description}


SPOT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "hours_quote": _nullable("string", "開館・営業時間の原文"),
        "closed_quote": _nullable("string", "休館日・定休日の原文（祝日の扱いを含める）"),
        "fee_adult_quote": _nullable("string", "大人料金の原文"),
        "fee_child_quote": _nullable("string", "子ども料金の原文"),
        "fee_senior_quote": _nullable("string", "高齢者料金の原文"),
        "reservation": {
            "type": "string",
            "enum": ["yes", "no", "partial", "unknown"],
            "description": "予約の要否",
        },
        "reservation_quote": _nullable("string", "予約について書かれた原文"),
        "address_quote": _nullable("string", "所在地の原文"),
        "phone_quote": _nullable("string", "電話番号の原文"),
        "visit_minutes_quote": _nullable("string", "所要時間の原文"),
        "title": _nullable("string", "30 字以内の見出し（自分の言葉で）"),
        "summary": _nullable("string", "100 字以内の要約（自分の言葉で）"),
    },
    "required": [
        "hours_quote",
        "closed_quote",
        "fee_adult_quote",
        "fee_child_quote",
        "fee_senior_quote",
        "reservation",
        "reservation_quote",
        "address_quote",
        "phone_quote",
        "visit_minutes_quote",
        "title",
        "summary",
    ],
    "additionalProperties": False,
}

NOTICE_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "kind": {
            "type": "string",
            "enum": ["closed", "open", "schedule_change"],
            "description": "告知の種類",
        },
        "date_quote": _nullable("string", "対象の日付・期間の原文"),
        "reason_quote": _nullable("string", "理由の原文"),
        "title": _nullable("string", "30 字以内の見出し（自分の言葉で）"),
    },
    "required": ["kind", "date_quote", "reason_quote", "title"],
    "additionalProperties": False,
}
# 共有ページ用。`facility_quote` で対象施設を名指しさせる。null なら捨てる（ADR 0010）
SHARED_NOTICE_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        **NOTICE_ITEM_SCHEMA["properties"],
        "facility_quote": _nullable(
            "string", "この告知の対象施設名の原文。決められなければ null（推測しない）"
        ),
    },
    "required": [*NOTICE_ITEM_SCHEMA["required"], "facility_quote"],
    "additionalProperties": False,
}
SHARED_NOTICE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"notices": {"type": "array", "items": SHARED_NOTICE_ITEM_SCHEMA}},
    "required": ["notices"],
    "additionalProperties": False,
}

NOTICE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"notices": {"type": "array", "items": NOTICE_ITEM_SCHEMA}},
    "required": ["notices"],
    "additionalProperties": False,
}

ROUTE_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "route_label": _nullable("string", "航路・路線名の原文（例: 高松港―宮浦港）"),
        "first_departure_quote": _nullable("string", "始発の時刻の原文"),
        "last_departure_quote": _nullable("string", "終発の時刻の原文"),
        "trips_quote": _nullable("string", "1 日の便数の原文（例: 1日13便）"),
        "duration_quote": _nullable("string", "所要時間の原文（例: 約50分）"),
        "fare_adult_quote": _nullable("string", "大人運賃の原文"),
        "fare_child_quote": _nullable("string", "小児運賃の原文"),
        "service_days_quote": _nullable("string", "運航日・ダイヤの原文（例: 土休日ダイヤ）"),
        "title": _nullable("string", "30 字以内の見出し（自分の言葉で）"),
        "summary": _nullable("string", "100 字以内の要約（自分の言葉で）"),
    },
    "required": [
        "route_label",
        "first_departure_quote",
        "last_departure_quote",
        "trips_quote",
        "duration_quote",
        "fare_adult_quote",
        "fare_child_quote",
        "service_days_quote",
        "title",
        "summary",
    ],
    "additionalProperties": False,
}
ROUTE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"routes": {"type": "array", "items": ROUTE_ITEM_SCHEMA}},
    "required": ["routes"],
    "additionalProperties": False,
}


def spot_summary_fallback(raw: dict[str, Any]) -> str:
    """要約が原文の写しだったときの差し替え。事実だけで書く（ADR 0002）。"""
    hours = raw.get("hours_quote")
    return (
        "開館時間と休館日は公式ページでご確認ください。"
        if not hours
        else "公式ページの情報から作成。"
    )


SPOT_SPEC = ExtractionSpec(
    name="spot",
    prompt_version=SPOT_PROMPT_VERSION,
    system_prompt=_load(SPOT_PROMPT_VERSION),
    output_schema=SPOT_SCHEMA,
    quote_fields=(
        QuoteField("hours_quote", "hours", parse_opening_hours),
        QuoteField("closed_quote", "closures", parse_closures),
        QuoteField("fee_adult_quote", "fee_adult", parse_fee),
        QuoteField("fee_child_quote", "fee_child", parse_fee),
        QuoteField("fee_senior_quote", "fee_senior", parse_fee),
        QuoteField("address_quote", "address", None),
        QuoteField("phone_quote", "phone", None),
        QuoteField("visit_minutes_quote", "visit_minutes", parse_minutes),
        QuoteField("reservation_quote", "reservation_note", None),
    ),
    items_key=None,  # 1 ページ = 1 施設
    free_text_fields=("title", "summary", "reservation"),
    summary_field="summary",
    summary_max_chars=120,
    verbatim_overlap_chars=30,
    summary_fallback=spot_summary_fallback,
    # 本文に営業時間が無い施設のための最後の一手。schema.org の JSON-LD に
    # 施設自身が書いた `openingHoursSpecification` があればそれを使う（sitemill ADR 0018 追記）。
    # 二十四の瞳映画村は本文に時間が無く、フッターの「AM9:00〜PM5:00」は TEL の隣にあって
    # 電話の受付時間とも読めるため根拠にできなかった。JSON-LD には 9:00–17:00 が書かれている
    structured_hours_target="hours",
)

NOTICE_SPEC = ExtractionSpec(
    name="notice",
    prompt_version=NOTICE_PROMPT_VERSION,
    system_prompt=_load(NOTICE_PROMPT_VERSION),
    output_schema=NOTICE_SCHEMA,
    quote_fields=(
        QuoteField("date_quote", "span", parse_notice_span),
        QuoteField("reason_quote", "reason", None),
    ),
    items_key="notices",
    free_text_fields=("title", "kind"),
    summary_field=None,
)

SHARED_NOTICE_SPEC = ExtractionSpec(
    name="shared_notice",
    prompt_version=SHARED_NOTICE_PROMPT_VERSION,
    system_prompt=_load(SHARED_NOTICE_PROMPT_VERSION),
    output_schema=SHARED_NOTICE_SCHEMA,
    quote_fields=(
        QuoteField("date_quote", "span", parse_notice_span),
        QuoteField("reason_quote", "reason", None),
        # 施設名も引用として検証する。ページに無い名前を書いた告知はここで落ちる
        QuoteField("facility_quote", "facility", None),
    ),
    items_key="notices",
    free_text_fields=("title", "kind"),
    summary_field=None,
)

ROUTE_SPEC = ExtractionSpec(
    name="route",
    prompt_version=ROUTE_PROMPT_VERSION,
    system_prompt=_load(ROUTE_PROMPT_VERSION),
    output_schema=ROUTE_SCHEMA,
    quote_fields=(
        QuoteField("route_label", "route_label", None),
        QuoteField("first_departure_quote", "first_departure", parse_time_of_day),
        QuoteField("last_departure_quote", "last_departure", parse_time_of_day),
        QuoteField("duration_quote", "duration_minutes", parse_minutes),
        QuoteField("fare_adult_quote", "fare_adult", parse_fee),
        QuoteField("fare_child_quote", "fare_child", parse_fee),
        QuoteField("service_days_quote", "service_days", parse_service_days),
    ),
    items_key="routes",
    free_text_fields=("title", "summary"),
    summary_field="summary",
    summary_max_chars=120,
)

SPECS_BY_KIND = {
    "spot_detail": SPOT_SPEC,
    "spot_hours": SPOT_SPEC,
    "spot_fees": SPOT_SPEC,
    "spot_access": SPOT_SPEC,
    "notice": NOTICE_SPEC,
    "shared_notice": SHARED_NOTICE_SPEC,
    "timetable": ROUTE_SPEC,
    "route_fares": ROUTE_SPEC,
}


def spec_for_kind(kind: str) -> ExtractionSpec | None:
    return SPECS_BY_KIND.get(kind)
