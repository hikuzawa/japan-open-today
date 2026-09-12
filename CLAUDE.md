# japan-open-today

sitemill の 2 つ目の利用者。香川県の観光施設と交通について「今日・今週、行けるか」を日本語・英語・繁体字で提供するサイト。
親フォルダの CLAUDE.md の配置ルールに従う。

## 構成
- `src/japan_open_today/` サービス定義（`service.py`）、抽出スキーマ（`schema.py`）、プロンプト（`prompts/`）、
  ページ生成（`pages.py`）、エリア区分（`areas.py`）、ASP 設定（`affiliates.py`）
- `data/sources/*.yaml` 施設・交通事業者と巡回 URL と巡回方針（一次設定。AI が書き、人はレビューだけ）
- `data/glossary/<locale>.yaml` 施設名・路線名の固定訳（公式表記が無い場合のみ使う。凍結して差分でレビューする）
- `data/reference/` 一次データの取得物（内閣府の祝日 CSV、市町コードなど。取得日を `SOURCES.md` に記す）
- `data/review/<pref>.yaml` レビュー待ち行列（運営主体が判定できなかった案件だけ）
- `data/state/` `data/records/` `data/runs/` パイプラインの状態と成果物（コミット対象）
- `data/raw/` `data/llm_cache/` ローカルキャッシュ（git 管理外）
- `i18n/<locale>.yaml` 画面の文言カタログ（sitemill の i18n）
- `templates/` Jinja2、`static/` CSS/JS、`dist/` 生成物（git 管理外）
- `site.toml` サイト設定。基準 URL はここ 1 か所だけで差し替える
- `.github/workflows/` 日次パイプライン（pipeline.yml, 05:00 JST）と push ごとの検査（checks.yml）。
  sitemill はタグ固定（現在 `v0.2.0`）。上げるときは 2 本の `ref:` を揃える
- `docs/adr/` 設計判断、`docs/human-tasks.md` 人間側で必要な作業
- `tests/fixtures/html/` 保存済み HTML（出典 URL と取得日を `SOURCES.md` に記す）、`tests/fixtures/eval/` 抽出精度の計測ケース

## コマンド（このディレクトリで実行）
- `uv sync` / `uv run pytest` / `uv run ruff check src tests`
- `uv run sitemill discover|crawl|extract|heal|build|run|eval` / `uv run sitemill deploy --dry-run`
- `uv run python -m tools.fetch_assets` 実体の無い採用画像を出典から取り直す（内容が変わっていたら採らない）
- `uv run python -m tools.sync_assets` 採用画像を `static/assets/` に複写（extract のあと build の前）
- `uv run python .github/scripts/check_public_urls.py` 公開 URL の検査（canonical / og:url / hreflang / sitemap / robots が `base_url` を指すか）
- `uv run python -m tools.report_coverage` 項目の充足率と判定の内訳（unknown の理由）を数える
- `uv run python -m tools.resolve_commons` / `tools.collect_sources` / `tools.probe_assets` 情報源と画像の調査
- `uv run python -m tools.collect_spots --enumerate --classify` 県の一覧から施設を数え上げて型を判定 →
  `tools.seed_spots --apply` で情報源にする → `tools.retype_spots` / `tools.add_listing_hours` で補正
- `uv run python -m tools.resolve_names --apply` / `tools.translate_names --apply` 多言語名の確定（ADR 0005）
- 生成物の確認は `dist/` の HTML をブラウザペインで直接開くか、`uv run python -m http.server -d dist 8000`

## 守ること

### 事実の扱い
- 営業時間・料金・所要時間などの事実は sitemill の quote-then-parse でのみ値にする。LLM の推定値を混ぜない
- 公式サイトの説明文は転載しない。紹介文は構造化した事実から各言語のテンプレートで自動生成する（ADR 0002）
- **断定できないことは断定しない**。「今日開いているか」は `open` / `closed` / `unknown` の 3 値で、
  材料が無いか矛盾していれば `unknown` と出して一次情報リンクへ送る（ADR 0004）
- 「今日」は日付を明記した形でしか書かない（「今日は開いています」ではなく「9 月 12 日（土）は 10:00–17:00 開館」）
- 判定には必ず根拠（根拠コード・原文の引用・一次情報 URL・取得日時）を併記する
- 公式ページの取得が 14 日間できていない施設は、規則上は開館日でも `unknown` に落とす（`stale_source`）

### 巡回
- **URL は推測で組み立てない。必ずリンクから辿る**（一覧の ID・スラッグから URL を作らない）。
  辿れなければ「情報源なし」として記録する。施設の素性を決めるページは、**その施設の名前が
  本文に出てくること**を確かめてから取り込む（`spot_detail` の門。ADR 0009 追記）。
  Commons のカテゴリ名も同じで、推測せず記事からのリンクを辿る
- 巡回するのは公式の情報源だけ: 施設の公式サイト、県・市町、観光協会、交通事業者。
  民間の予約サイト・まとめサイトは巡回しない（`third_party` と `unknown` は絶対に巡回しない）
- 運営主体の根拠（引用と URL、確認日）を必ず `sources/*.yaml` に残す（ADR 0009）
- お知らせ・運休告知のページは変化率に関わらず**毎日**取りに行く。営業時間・料金は変化率に従う（ADR 0007）
- robots.txt を守り、ホストごとに間隔を空ける。取得した生 HTML はコミットしない

### 人間レビュー
- **人間レビューに回すのは、AI が取れる情報をすべて取った後も運営主体を判定できない案件だけ**にする。
  公的な施設一覧からのリンク、公式サイト内の運営者表記、ドメイン種別で判定できたら自動で採用する
- レビュー待ちが 10 件を超えたら「発見側の作り込み不足」とみなし、人に配る前に発見・判定側を直す
- 人に回すときは、試した情報源と失敗の理由を必ず添える

### 多言語
- 事実は構造化データからロケール別に描画する。描画時に LLM を使わない（ビルドは再現する）
- 施設名は「公式が提供する表記 → 用語集の固定訳 → 日本語表記のまま」の順。ローマ字化した推測名は作らない。
  訳の確定は `tools/resolve_names.py`（公式ページ → 自治体・観光協会 → Wikipedia の言語間リンク）と
  `tools/translate_names.py`（固定訳）で行い、`data/glossary/<locale>.yaml` に由来つきで凍結する（ADR 0005 追記）
- 表示名は `site.toml` の `name`（正式名 Japan Open Today）に固定し、各言語の短い表記は
  `i18n/<locale>.yaml` の `site.name_short`（日本語=今日行ける日本 / 繁体字=今天能去的日本）で差し替える
- 全ページに hreflang を相互に付ける。`x-default` は日本語

### 画像・視覚要素
- 画像の採用経路は 4 つだけ（自治体・観光協会のフリー素材／Wikimedia Commons の CC BY・CC0／公式 SNS の埋め込み／
  Maps の埋め込み）。曖昧なものは不採用。登録済み資産でない `<img>` があればビルドを止める
- **実在の場所を AI 画像生成で描かない**。図解は SVG のコード生成に限り「自動生成」と明記する
- 写真が無くても成立するデザインを保つ。空の写真枠や「写真なし」の板は出さない

### サイト全体
- すべてのページに信頼シグナル（更新日時・一次情報リンク・運営者・件数）を出す。欠けるとビルドが失敗する
- アフィリエイトは契約が済むまで CTA を「準備中」にする。ダミーリンクは置かない
- 秘密情報は `.env` にだけ置く（手で書く。パスワードマネージャーや環境変数を探索しない）。
  鍵が無ければ止めて「.env に何を書くか」を提示する
- `.env.example` にはプレースホルダー（空の値）だけを置く。値を書いた時点でコミット前フックが止める
- コミット前フックは `.githooks/pre-commit`。clone 後に一度 `git config core.hooksPath .githooks` で有効化する

### sitemill との関係
- 汎用ロジックは sitemill へ、固有のものはここへ（ADR 0008）。迷ったら「他のサービスでも使うか」で判断する
- **sitemill への変更は後方互換の追加だけにする**。akiya-atlas の CI はタグ固定だが手元開発は path 依存なので、
  破壊的変更は akiya-atlas の手元のテストを壊す。sitemill を触ったら sitemill と akiya-atlas の両方のテストを通す
- akiya-atlas のコードは触らない

### 進め方
- 対話・報告・質問は日本語で行う（コードとコミットメッセージは英語でよい）
- 区切りごとに `uv run pytest` と `uv run ruff check` を通してからコミットする。コミットはこのディレクトリ内で行う。
  コミット前に `git pull --rebase` する
