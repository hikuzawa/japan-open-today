"""抽出結果をレコードにする（ADR 0002）。

要点は 3 つ。
- 項目ごとに「parsed を優先し、詳細ページ由来を一覧ページ由来より優先」で統合する
- ページ種別ごとに**取得日時を別に記録する**。開館時間と告知は鮮度の意味が違う（ADR 0004・0007）
- 告知は期間が読めたものだけ残す。相対表現（「本日」）から日付を作らない
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from sitemill.clock import jst_today
from sitemill.extract import ExtractedItem
from sitemill.models import Provenance, Source
from sitemill.models.schedule import DateSpan, Evidence, NoticeKind, SpecialNotice
from sitemill.settings import Workspace
from sitemill.store.records import RecordStore

from japan_open_today.data import load_entries, records_path, routes_from_entry, spot_from_entry
from japan_open_today.schema import Fee, record_id_for

log = logging.getLogger(__name__)

# その種別のページから取れる項目（他の種別で上書きさせない）
FEE_FIELDS = {"fee_adult": "adult", "fee_child": "child", "fee_senior": "senior"}
SPOT_KINDS = ("spot_detail", "spot_hours", "spot_fees", "spot_access")
# 期間の終わりからこの日数を過ぎた告知は落とす（古い休業告知を出し続けない）
NOTICE_KEEP_DAYS = 3


def _evidence(url: str, quote: str | None, provenance: Provenance) -> Evidence:
    return Evidence(quote=quote, source_url=url, fetched_at=provenance.fetched_at)


def _spot_content(
    item: ExtractedItem, *, url: str, kind: str, provenance: Provenance
) -> dict[str, Any]:
    """施設のページ 1 枚から取れた内容。取れなかった項目は入れない（上書きしない）。"""
    content: dict[str, Any] = {}
    hours = item.fields.get("hours")
    if hours is not None and hours.ok:
        periods = []
        for period in hours.value:  # type: ignore[union-attr]
            dumped = period.model_dump()
            dumped["evidence"] = _evidence(url, hours.quote, provenance).model_dump(mode="json")
            periods.append(dumped)
        content["hours"] = periods
        content["hours_fetched_at"] = provenance.fetched_at.isoformat()
    closures = item.fields.get("closures")
    if closures is not None and closures.ok:
        rules = []
        for rule in closures.value:  # type: ignore[union-attr]
            dumped = rule.model_dump()
            dumped["evidence"] = _evidence(url, closures.quote, provenance).model_dump(mode="json")
            rules.append(dumped)
        content["closures"] = rules
        content.setdefault("hours_fetched_at", provenance.fetched_at.isoformat())

    fees: list[dict[str, Any]] = []
    for field_name, category in FEE_FIELDS.items():
        fv = item.fields.get(field_name)
        if fv is None or not fv.ok:
            continue
        fees.append(
            Fee(category=category, amount=fv, note=None).model_dump(mode="json")  # type: ignore[arg-type]
        )
    if fees:
        content["fees"] = fees

    for field_name in ("address", "phone", "visit_minutes"):
        fv = item.fields.get(field_name)
        if fv is not None and fv.ok:
            content[field_name] = fv.model_dump(mode="json")

    reservation = item.free.get("reservation")
    if reservation in ("yes", "no", "partial", "unknown") and reservation != "unknown":
        content["reservation_required"] = reservation
    note = item.fields.get("reservation_note")
    if note is not None and note.ok:
        content["reservation_quote"] = note.value

    content["_page_kind"] = kind
    return content


def _notices(
    items: Sequence[ExtractedItem], *, url: str, provenance: Provenance
) -> list[dict[str, Any]]:
    """告知。期間が読めたものだけを残す。"""
    out: list[dict[str, Any]] = []
    for item in items:
        span = item.fields.get("span")
        if span is None or not span.ok:
            continue
        start_text, _, end_text = str(span.value).partition("/")
        try:
            notice = SpecialNotice(
                kind=NoticeKind(item.free.get("kind") or "closed"),
                span=DateSpan(
                    start=date.fromisoformat(start_text), end=date.fromisoformat(end_text)
                ),
                reason=item.free.get("title") or None,
                evidence=_evidence(url, span.quote, provenance),
            )
        except (ValueError, KeyError) as e:
            log.info("%s: 告知を読めないので捨てる（%s）", url, e)
            continue
        out.append(notice.model_dump(mode="json"))
    return out


def merge_record(existing: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """項目ごとに統合する。詳細ページ由来を優先し、取れなかった項目は既存を残す。"""
    out = dict(existing)
    new_kind = new.get("_page_kind", "")
    old_kind = existing.get("_page_kind", "")
    detail_wins = old_kind == "spot_detail" and new_kind != "spot_detail"
    for key, value in new.items():
        if key == "_page_kind":
            continue
        if key == "notices":
            # 告知は積み上げる（別のお知らせページから別の告知が来る）
            merged = {
                (n.get("evidence") or {}).get("quote", "") or str(i): n
                for i, n in enumerate([*existing.get("notices", []), *value])
            }
            out["notices"] = list(merged.values())
            continue
        if detail_wins and key in existing and existing[key]:
            continue
        out[key] = value
    out["_page_kind"] = new_kind if not detail_wins else old_kind
    return out


def ingest_items(
    ws: Workspace,
    *,
    store: RecordStore,
    source: Source,
    url: str,
    kind: str,
    items: Sequence[ExtractedItem],
    provenance: Provenance,
) -> dict[str, int]:
    counts = {"created": 0, "updated": 0, "unchanged": 0, "skipped": 0}
    entry = next((e for e in load_entries(ws) if e["id"] == source.id), None)
    if entry is None:
        return counts
    now = provenance.extractor.extracted_at if provenance.extractor else provenance.fetched_at

    if kind in SPOT_KINDS:
        spot = spot_from_entry(entry)
        if spot is None or not items:
            counts["skipped"] += 1
            return counts
        content = _spot_content(items[0], url=url, kind=kind, provenance=provenance)
        content["spot_id"] = spot.spot_id
        result = store.upsert(
            record_id_for(source.id, spot.spot_id),
            content,
            now=now,
            provenance=provenance.model_dump(mode="json"),
            merge=merge_record,
        )
        counts[result] += 1
        return counts

    if kind == "notice":
        notices = _notices(items, url=url, provenance=provenance)
        targets = [s.spot_id for s in [spot_from_entry(entry)] if s is not None]
        targets += [r.route_id for r in routes_from_entry(entry)]
        if not targets:
            counts["skipped"] += 1
            return counts
        for key in targets:
            content: dict[str, Any] = {
                "notices": notices,
                "notices_fetched_at": provenance.fetched_at.isoformat(),
            }
            content["spot_id" if key == targets[0] and entry.get("spot") else "route_id"] = key
            result = store.upsert(
                record_id_for(source.id, key),
                content,
                now=now,
                provenance=provenance.model_dump(mode="json"),
                merge=merge_record,
            )
            counts[result] += 1
        return counts

    counts["skipped"] += len(items) or 1
    return counts


def finalize_records(ws: Workspace, *, now: datetime) -> dict[str, int]:
    """期限の切れた告知を落とす。古い休業告知を出し続けないため。"""
    counts = {"notices_dropped": 0, "records": 0}
    today = jst_today(now)
    for entry in load_entries(ws):
        store = RecordStore(records_path(ws, entry["id"]))
        if not len(store):
            continue
        changed = False
        for record in store.records.values():
            counts["records"] += 1
            notices = record.get("notices") or []
            kept = []
            for notice in notices:
                end = (notice.get("span") or {}).get("end")
                if end and (today - date.fromisoformat(end)).days > NOTICE_KEEP_DAYS:
                    counts["notices_dropped"] += 1
                    changed = True
                    continue
                kept.append(notice)
            if changed:
                record["notices"] = kept
        if changed:
            store.save()
    return counts
