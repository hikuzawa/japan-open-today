"""sitemill の Service 実装。データ・スキーマ・ページ構成はこちらが持つ（sitemill ADR 0006）。"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from sitemill.assets import Asset, AssetStore
from sitemill.extract import ExtractedItem, ExtractionSpec
from sitemill.models import OperatorKind, Page, Provenance, Redirect, Source
from sitemill.settings import Workspace
from sitemill.store.records import RecordStore

from japan_open_today.data import (
    Dataset,
    load_entries,
    load_sources,
    records_path,
    spot_from_entry,
)
from japan_open_today.ingest import ingest_items, merge_record
from japan_open_today.spec import spec_for_kind

log = logging.getLogger(__name__)


def _spot_texts(spot: Any) -> list[str]:
    """その施設の記録に出てくる文字列（値と原文の引用）をすべて集める。"""
    out: list[str] = []

    def push(value: Any) -> None:
        if isinstance(value, str):
            out.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                push(item)
        elif isinstance(value, list | tuple):
            for item in value:
                push(item)

    push(spot.model_dump(mode="json"))
    return out


class JapanOpenTodayService:
    id = "japan-open-today"

    # 情報源の pages が巡回対象のすべて（発見は YAML に seed を書き足す形で行う）。
    # これを宣言すると、seed から外した URL の巡回状態が捨てられる。
    # 間違った URL を外しても記録が入り続ける事故を防ぐため（ADR 0009 追記）
    declared_pages_only = True

    # 巡回してよい運営主体（ADR 0009）。sitemill の既定（自治体だけ）より広い
    crawlable_operator_kinds = (
        OperatorKind.municipality,
        OperatorKind.municipality_affiliated,
        OperatorKind.prefecture,
        OperatorKind.tourism_association,
        OperatorKind.facility_official,
        OperatorKind.transport_operator,
    )

    def sources(self, ws: Workspace) -> list[Source]:
        return load_sources(ws)

    def extraction_spec(self, kind: str) -> ExtractionSpec | None:
        return spec_for_kind(kind)

    def ingest(
        self,
        ws: Workspace,
        *,
        source: Source,
        url: str,
        kind: str,
        items: Sequence[ExtractedItem],
        provenance: Provenance,
    ) -> dict[str, int]:
        store = RecordStore(records_path(ws, source.id))
        counts = ingest_items(
            ws, store=store, source=source, url=url, kind=kind, items=items, provenance=provenance
        )
        store.save()
        log.info("%s %s（%s）: %s", source.id, url, kind, counts)
        return counts

    def finalize(self, ws: Workspace, *, now: datetime) -> dict[str, int]:
        """期限切れの告知を落とし、鮮度の記録を更新する。"""
        from japan_open_today.ingest import finalize_records

        return finalize_records(ws, now=now)

    def pages(self, ws: Workspace, *, now: datetime) -> list[Page]:
        from japan_open_today import pages as page_builder

        return page_builder.build_pages(ws, Dataset.load(ws), now=now)

    def search_index(self, ws: Workspace) -> Any:
        from japan_open_today import pages as page_builder

        return page_builder.search_index(ws, Dataset.load(ws))

    def redirects(self, ws: Workspace) -> list[Redirect]:
        return []

    def eval_dir(self, ws: Workspace) -> Path | None:
        return ws.fixtures_dir / "eval"

    def assets(self, ws: Workspace) -> list[Asset]:
        """登録済みの画像資産（sitemill ADR 0020）。ページに出せるのはここにあるものだけ。"""
        root = ws.data_dir / "assets"
        if not root.is_dir():
            return []
        out: list[Asset] = []
        for directory in sorted(p for p in root.iterdir() if p.is_dir()):
            out.extend(AssetStore(root, directory.name).load().values())
        return out

    def pii_policy(self, ws: Workspace) -> Any:
        """施設の公式の連絡先（電話・メール）は公開情報なので、公開前の検査では許可する。

        電話番号は `phone` に入るとは限らず、所在地の原文に混ざっていることがある
        （金刀比羅宮の実例）。施設が自分の公式ページに載せている代表番号なので、
        `phone` と `address` の両方から拾って許可する。第三者の番号は載せない設計なので、
        ここで許可するのは施設自身の連絡先だけ。
        """
        from sitemill.build.pii import EMAIL_RE, allow_also, default_jp_gov_policy, find_phones

        phones: list[str] = []
        emails: list[str] = []
        for spot in Dataset.load(ws).spots:
            # 番号は `phone` に入るとは限らない。所在地の原文（金刀比羅宮）や、開館時間・
            # 料金・予約の引用（「お電話での受付 9:00〜17:00」）に混ざっていることがある。
            # いずれも施設が自分の公式ページに載せている代表番号なので許可する
            for text in _spot_texts(spot):
                # `find_phones` はダッシュを正規化してから探す。公式ページは「087ー823ー5023」の
                # ように長音記号で書くことがあり、正規表現を直接当てると 1 件も拾えない
                phones.extend(find_phones(text))
                # 施設が自分の公式ページに載せている問い合わせ先のメールも同じ扱いにする
                emails.extend(EMAIL_RE.findall(text))
        # 運営主体の根拠の引用も /data/ に出る。運営者の代表番号・FAX が入っていることがある
        for entry in load_entries(ws):
            quote = (entry.get("operator_evidence") or {}).get("quote") or ""
            phones.extend(find_phones(quote))
            emails.extend(EMAIL_RE.findall(quote))
        return allow_also(default_jp_gov_policy(), phones=phones, emails=emails)

    def review_candidates(self, ws: Workspace) -> list[dict[str, Any]]:
        """運営主体を判定できなかった情報源（ADR 0009）。10 件を超えたら発見側を直す。"""
        out: list[dict[str, Any]] = []
        from japan_open_today.data import load_entries

        for entry in load_entries(ws):
            if entry.get("policy") != "pending":
                continue
            spot = spot_from_entry(entry)
            out.append(
                {
                    "key": entry["id"],
                    "label": entry["name"],
                    "url": entry["official_url"],
                    "page_class": "spot" if spot is not None else "transport",
                    "operator_kind": entry.get("operator_kind", "unknown"),
                    "reason": " ".join((entry.get("notes") or "").split()),
                    "proposed_policy": "pending",
                }
            )
        return out


service = JapanOpenTodayService()

__all__ = ["JapanOpenTodayService", "merge_record", "service"]
