"""抽出結果をレコードにする（ADR 0002）。

要点は 3 つ。
- 項目ごとに「parsed を優先し、詳細ページ由来を一覧ページ由来より優先」で統合する
- ページ種別ごとに**取得日時を別に記録する**。開館時間と告知は鮮度の意味が違う（ADR 0004・0007）
- 告知は期間が読めたものだけ残す。相対表現（「本日」）から日付を作らない
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from sitemill.clock import jst_today
from sitemill.diff.normalize import squash
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
            dumped = period.model_dump(mode="json")
            dumped["evidence"] = _evidence(url, hours.quote, provenance).model_dump(mode="json")
            periods.append(dumped)
        content["hours"] = periods
        content["hours_fetched_at"] = provenance.fetched_at.isoformat()
    closures = item.fields.get("closures")
    if closures is not None and closures.ok:
        rules = []
        for rule in closures.value:  # type: ignore[union-attr]
            dumped = rule.model_dump(mode="json")
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

    if kind == "shared_notice":
        return _ingest_shared_notices(
            ws,
            store=store,
            entry=entry,
            items=items,
            url=url,
            provenance=provenance,
            now=now,
            counts=counts,
        )

    if kind in ("timetable", "route_fares"):
        routes = routes_from_entry(entry)
        if not routes:
            counts["skipped"] += 1
            return counts
        for content in _route_contents(items, routes, url=url, provenance=provenance):
            result = store.upsert(
                record_id_for(source.id, content["route_id"]),
                content,
                now=now,
                provenance=provenance.model_dump(mode="json"),
                merge=merge_record,
            )
            counts[result] += 1
        if not counts["created"] and not counts["updated"]:
            counts["skipped"] += 1
        return counts

    counts["skipped"] += len(items) or 1
    return counts


def _covered_spots(ws: Workspace, entry: dict[str, Any]) -> list[Any]:
    """共有ページが扱う施設。`covers` に書いた spot_id を他の情報源から引く。"""
    wanted = list(entry.get("covers") or [])
    if not wanted:
        return []
    by_id = {}
    for other in load_entries(ws):
        spot = spot_from_entry(other)
        if spot is not None:
            by_id[spot.spot_id] = spot
    missing = [sid for sid in wanted if sid not in by_id]
    if missing:
        raise ValueError(f"{entry['id']} の covers に未知の spot_id: {missing}")
    return [by_id[sid] for sid in wanted]


def _spot_names(spot: Any) -> list[str]:
    """照合に使う表記。日本語の表示名と別名だけ（英語・繁体字の表記は本文に出ない）。"""
    out = [spot.names["ja"].text] if "ja" in spot.names else []
    out.extend(spot.aliases)
    return [n for n in out if n]


def match_facility(quote: str, spots: Sequence[Any]) -> Any | None:
    """告知が名指しした施設を 1 つに決める。決まらなければ None。

    共有ページでは、この割り当てを間違えると**開いている施設のページに「休館」と出る**。
    だから「たぶんこれ」で当てない。完全一致 → 包含関係、の順で見て、候補が 1 つに
    絞れないときは None を返して告知を捨てる。
    """
    target = squash(quote or "")
    if not target:
        return None
    exact = [s for s in spots if any(squash(n) == target for n in _spot_names(s))]
    if len(exact) == 1:
        return exact[0]
    if exact:
        return None  # 同名の施設が 2 つある。決められない
    loose = [
        s
        for s in spots
        if any(squash(n) and (squash(n) in target or target in squash(n)) for n in _spot_names(s))
    ]
    return loose[0] if len(loose) == 1 else None


def _ingest_shared_notices(
    ws: Workspace,
    *,
    store: RecordStore,
    entry: dict[str, Any],
    items: Sequence[ExtractedItem],
    url: str,
    provenance: Provenance,
    now: datetime,
    counts: dict[str, int],
) -> dict[str, int]:
    """1 枚で複数施設を扱うお知らせページを、施設ごとに切り分けて取り込む（ADR 0010）。

    ベネッセアートサイトのカレンダーは、地中美術館・ベネッセハウス・豊島美術館などの
    休館日を 1 ページに並べている。S5 ではこのページを 1 施設に紐づけるしかなく、
    他館の休館をその館の休館として公開してしまうため、情報源から外していた。
    ここでは**告知が名指しした施設名**で割り当て、名指しが無いか決められない告知は捨てる。
    """
    spots = _covered_spots(ws, entry)
    if not spots:
        counts["skipped"] += 1
        return counts
    per_spot: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        facility = item.fields.get("facility")
        quote = facility.value if facility is not None and facility.ok else None
        spot = match_facility(str(quote or ""), spots)
        if spot is None:
            counts["unattributed"] = counts.get("unattributed", 0) + 1
            log.info("%s: 施設を決められない告知を捨てる（引用=%r）", url, quote)
            continue
        rows = _notices([item], url=url, provenance=provenance)
        if not rows:
            counts["skipped"] += 1
            continue
        per_spot.setdefault(spot.spot_id, []).extend(rows)
    for spot_id, rows in per_spot.items():
        content: dict[str, Any] = {
            "spot_id": spot_id,
            "notices": rows,
            "notices_fetched_at": provenance.fetched_at.isoformat(),
        }
        result = store.upsert(
            record_id_for(entry["id"], spot_id),
            content,
            now=now,
            provenance=provenance.model_dump(mode="json"),
            merge=merge_record,
        )
        counts[result] += 1
    return counts


def _route_contents(
    items: Sequence[ExtractedItem],
    routes: Sequence[Any],
    *,
    url: str,
    provenance: Provenance,
) -> list[dict[str, Any]]:
    """時刻表ページの抽出結果を、YAML に書いた航路・路線に割り当てる。

    ページには複数の航路が並ぶ（「高松港―宮浦港」「宇野港―本村」など）。**原文の航路名と
    起終点の地名が一致したものだけ**に割り当てる。どれとも一致しない行は捨てる。
    地名が合わないものを「たぶんこれ」で当てると、別の航路の時刻を出してしまう。
    """
    out: list[dict[str, Any]] = []
    for route in routes:
        matched = None
        for item in items:
            label = item.value("route_label") or ""
            if label and _matches_route(route, label):
                matched = item
                break
        if matched is None:
            continue
        content: dict[str, Any] = {"route_id": route.route_id}
        for field_name, target in (
            ("first_departure", "first_departure"),
            ("last_departure", "last_departure"),
            ("duration_minutes", "duration_minutes"),
        ):
            fv = matched.fields.get(field_name)
            if fv is not None and fv.ok:
                content[target] = fv.model_dump(mode="json")
        fares = []
        for field_name, category in (("fare_adult", "adult"), ("fare_child", "child")):
            fv = matched.fields.get(field_name)
            if fv is not None and fv.ok:
                fares.append(
                    Fee(category=category, amount=fv).model_dump(mode="json")  # type: ignore[arg-type]
                )
        if fares:
            content["fares"] = fares
        days = matched.fields.get("service_days")
        if days is not None and days.ok:
            content["service_days"] = days.value.model_dump(mode="json")  # type: ignore[union-attr]
            content["service_days_quote"] = days.quote
        content["evidence"] = Evidence(
            quote=matched.value("route_label"),
            source_url=url,
            fetched_at=provenance.fetched_at,
        ).model_dump(mode="json")
        content["_page_kind"] = "timetable"
        out.append(content)
    return out


_BRACKETS = re.compile(r"[（）()［］\[\]・／/]+")


def _tokens(node: str) -> list[str]:
    """「直島（宮浦港）」→ ["直島", "宮浦"]。括弧と「港」「駅」の有無を無視する。"""
    parts = _BRACKETS.split(node.replace("港", "").replace("駅", ""))
    return [p.strip() for p in parts if p.strip()]


def _matches_route(route: Any, label: str) -> bool:
    """原文の航路名・路線名が、この航路のものか。

    起終点の地名で照合する（「高松港―宮浦港(直島)」と「高松港」「直島（宮浦港）」）。
    起終点が書かれていない路線名だけの表記（「坂手線」）は、YAML の路線名との一致で見る。
    どちらにも当てはまらない行は捨てる。「たぶんこれ」で当てると別の航路の時刻を出してしまう。
    """
    stripped = label.replace("港", "").replace("駅", "")
    ends = [n for n in (route.from_node, route.to_node) if n]
    if ends and all(any(tok in stripped for tok in _tokens(node)) for node in ends):
        return True
    for name in route.names.values():
        text = getattr(name, "text", None) or (name.get("text") if isinstance(name, dict) else None)
        if text and text.replace("港", "").replace("駅", "") in stripped:
            return True
    return False


def finalize_records(ws: Workspace, *, now: datetime) -> dict[str, int]:
    """期限の切れた告知と、情報源から外した URL 由来の告知を落とす。

    2 つめが要るのは、間違った情報源を外しても**記録に残った告知はページに出続ける**ためである。
    ベネッセの共有お知らせページを 1 施設の notice として seed していた間に取り込んだ告知は、
    他館の休業である可能性がある。seed を外したら、その URL 由来の告知も消す。
    """
    counts = {"notices_dropped": 0, "records": 0, "notices_unseeded": 0}
    today = jst_today(now)
    for entry in load_entries(ws):
        store = RecordStore(records_path(ws, entry["id"]))
        if not len(store):
            continue
        seeded = {p["url"] for p in entry.get("pages", []) or []}
        changed = False
        for record in store.records.values():
            counts["records"] += 1
            notices = record.get("notices") or []
            kept = []
            for notice in notices:
                source_url = (notice.get("evidence") or {}).get("source_url")
                if source_url and source_url not in seeded:
                    counts["notices_unseeded"] += 1
                    changed = True
                    log.info("%s: seed から外れた %s 由来の告知を落とす", entry["id"], source_url)
                    continue
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
