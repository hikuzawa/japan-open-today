# ADR 0002: 施設・交通のスキーマと、紹介文を事実から自動生成する方針

- ステータス: 採用（2026-09-12）

## 背景
公式サイトの説明文は転載しない。営業時間・料金などの事実は原文からの機械抽出だけで持ち、一次情報 URL と取得日時を添えて表示する。
「今日行けるか」を計算するには、時間そのものではなく**時間の規則**を構造化して持つ必要がある。

## 決定

### 値の持ち方
数値・時刻・日付・金額は例外なく sitemill の `FieldValue`（LLM が返した**原文の引用**＋決定的パーサが作った値）で持つ。
LLM に数値を決めさせない（sitemill ADR 0004 の quote-then-parse）。引用が原文に無ければ値にしない。

### 施設 `Spot`
```
spot_id, area, category(museum|shrine_temple|park|garden|viewpoint|onsen|castle|other)
names: {ja|en|zh-Hant: {text, source: official|glossary, evidence_url}}   # ADR 0005
official_url, operator, operator_kind, operator_evidence{quote, url, checked_on}
address: FieldValue[str]     # 施設は公開情報なので番地まで持つ
phone:   FieldValue[str]     # 公式の代表電話。公開前の PII 検査には許可リストで通す
hours:         list[HoursPeriod{applies_to(曜日/季節), open, close, last_admission, quote}]
closed_rules:  list[ClosureRule{kind(weekly|nth_weekday|annual_range|date), value,
                                holiday_behavior(open|next_weekday|closed), quote}]
special_closures: list[SpecialClosure{date_range, reason, quote, url, fetched_at}]  # 告知ページ由来
fees:  list[Fee{category(adult|university|highschool|child|senior|free), amount: FieldValue[int], quote}]
reservation: {required(yes|no|partial|unknown), url, quote}
access: list[AccessLeg{from_node, mode, line, duration_minutes: FieldValue[int], quote}]
visit_minutes: FieldValue[int]        # 所要時間。第 2 フェーズの旅程生成で使う
map_query, embeds, provenance, status, first_seen_at, last_seen_at
```

### 交通 `TransportOperator` / `Route` / `ServiceNotice`
```
operator: id, name(多言語), kind(ferry|rail|bus), official_url, notice_url, operator_evidence
route:    id, operator_id, name, mode, from_node, to_node,
          first_departure/last_departure: FieldValue[time], trips_per_day: FieldValue[int],
          duration_minutes: FieldValue[int], fares: list[Fee],
          service_days: list[ClosureRule], timetable_url
notice:   id, operator_id, route_ids, kind(suspension|delay|schedule_change|normal),
          date_range, quote, url, fetched_at
```

### 一覧と詳細の統合
同じ `spot_id` に複数ページ（一覧・詳細・料金・アクセス・お知らせ）から抽出した内容が入る。
項目ごとに「parsed を優先、詳細ページ由来を一覧ページ由来より優先」で統合する（akiya-atlas ADR 0002 と同じ流儀）。
一定期間（既定 30 日）どのページからも見つからないレコードは削除せず `stale` にする。

### 紹介文
- 公式の説明文は転載しない。**紹介文は構造化した事実から各言語のテンプレートで自動生成する**
  （例: 「直島にある美術館。10:00–17:00 開館、月曜休館。大人 2,100 円。」）
- 生成に描画時の LLM は使わない。同じデータからは常に同じ文になる（ビルドが再現する）
- LLM が返した自由文（`title` / `summary`）は、原文と 30 文字以上の逐語一致があれば転載とみなして捨て、
  事実からの定型文に置き換える（sitemill の `ExtractionSpec` の既定の仕組みをそのまま使う）

## 影響
- 時間・休業の構造化には日本語の決定的パーサが必要なので、sitemill に `parse/jp/hours` などを足す（ADR 0008）
- `phone` を持つため、公開前の PII 検査には施設の公式電話を許可する `pii_policy` フックを実装する
  （akiya-atlas が自治体の代表電話を許可しているのと同じ仕組み）
