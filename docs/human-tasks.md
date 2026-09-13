# 人間側で必要な作業（2026-09-12 起票）

AI が全自動で回すための前提として、アカウント作成や鍵の登録など人にしかできない作業を並べる。
上から順に済ませると、`uv run sitemill run` → 日次の GitHub Actions → Cloudflare Pages 公開までがつながる。

## 今すぐ必要（縦断パイプラインを完走させるため）
1. **Anthropic API キー**を `japan-open-today/.env` に書く（`ANTHROPIC_API_KEY=`）。
   これが無いと `sitemill extract` は「.env に何を書くか」を表示して止まる。
   抽出モデルは `site.toml` の `[llm] model`（既定 `claude-haiku-4-5`）で変えられる。
   akiya-atlas の `.env` の鍵をそのまま流用してもよいが、サービスごとに分けると使用量が切り分けられる。
2. ~~**GitHub リポジトリ**を作成し push する~~ → S0 で作成済み（private: `hikuzawa/japan-open-today`）。

## 公開までに必要
3. ~~**ドメイン `japan-open-today.com`** を取得し、Pages のカスタムドメイン（apex と www）と
   Bulk Redirects（www と pages.dev → apex、301）を設定する~~ → 完了（2026-09-13）。
   `site.toml` の `base_url` と `.github/workflows/pipeline.yml` の `--expect-base` は
   `https://japan-open-today.com` に切り替え済み。
4. **Cloudflare アカウント**と API トークン（Account > Cloudflare Pages: Edit）・アカウント ID。
   akiya-atlas で発行済みのものを流用できる。Pages プロジェクト `japan-open-today` は CI が「無ければ作成」する。
   **注意**: `japan-open-today.pages.dev` が第三者に使われている場合、既定ホストは
   `japan-open-today-<4文字>.pages.dev` になる（akiya-atlas で実際に起きた）。CI の出力で確認して `base_url` を直す。
5. **GitHub Secrets** を `hikuzawa/japan-open-today` に登録する（下表）。
6. **Cloudflare Web Analytics** を有効にしてビーコントークンを取得し、`CF_WEB_ANALYTICS_TOKEN` に入れる
   （無くても動く。計測タグが出ないだけ）。
7. ~~**Google Maps Platform**: Maps Embed API を有効にし、HTTP リファラで公開ホストに制限したキーを
   `GOOGLE_MAPS_EMBED_KEY` に入れる~~ → 完了（2026-09-13）。リファラ制限に
   `japan-open-today.com` と `japan-open-today.pages.dev` の両方が入っている。
   ホストを増やすときはここも足す（足さないと地図が外部リンクに落ちる）。
8. ~~**連絡先フォームを作る**~~ → 完了（2026-09-14）。`site.toml` の `[operator] contact` に
   素の URL、`i18n/<locale>.yaml` の `contact.url` に言語別の prefill 付き URL を入れて配置済み。
   スクリプトプロパティの値（API キー・GitHub トークン）は Apps Script 側にだけあり、
   このリポジトリには無い。
9. ~~公開後: **Google Search Console** の登録~~ → 完了（2026-09-13、ドメインプロパティ）。
   検索パフォーマンスとインデックス状況の取り込みも入った（sitemill ADR 0023）。
   - サービスアカウント `sitemill-search-console@japan-open-today.iam.gserviceaccount.com` を
     両プロパティに「制限付き」で追加済み
   - 鍵は `.env` と GitHub Secrets の `GOOGLE_SEARCH_CONSOLE_KEY`（JSON を base64 にした 1 行）
   - 日次パイプラインが取り込み、週次まとめ（`weekly.yml`）が Issue に出す
10. ~~公開後: **www と pages.dev から apex への 301**~~ → 完了（2026-09-13、Bulk Redirects）。

## 収益化のために必要
11. **アフィリエイトの申込み**（審査に日数がかかるので早めに）。
    - **Klook**（アクティビティ・入場券）: 施設ページの枠に使う
    - **Agoda** / **Booking.com**: エリアページの宿泊枠に使う
    契約が済むまで CTA は「準備中」表示で、ダミーリンクは置かない（ADR 0006）。
12. 各 ASP の掲載ルール（「広告」表記、ロゴの使用条件など）に合わせてテンプレートの文言を確認する。

## 任意・後で
13. Street View を使う場合は Geocoding API の有効化（所在地から緯度経度を得るため）。
14. 公式 SNS（YouTube / Instagram / X）の埋め込みを使う場合、Instagram と X は oEmbed の利用登録が要る。

## GitHub Secrets の登録コマンド
値は画面に出さず、ファイルや標準入力から流し込む。登録後は `gh secret list --repo hikuzawa/japan-open-today` で確認する。

| Secret | 値の発行場所 | 登録コマンド |
|---|---|---|
| `ANTHROPIC_API_KEY` | Anthropic Console → API Keys | `grep '^ANTHROPIC_API_KEY=' .env \| cut -d= -f2- \| gh secret set ANTHROPIC_API_KEY --repo hikuzawa/japan-open-today` |
| `CLOUDFLARE_API_TOKEN` | Cloudflare → My Profile → API Tokens → テンプレート「Cloudflare Pages — Edit」 | `gh secret set CLOUDFLARE_API_TOKEN --repo hikuzawa/japan-open-today` |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare → Workers & Pages の概要ページ右側「Account ID」 | `gh secret set CLOUDFLARE_ACCOUNT_ID --repo hikuzawa/japan-open-today` |
| `GOOGLE_MAPS_EMBED_KEY`（任意） | Google Cloud Console → Credentials。Maps Embed API のみ、HTTP リファラで制限 | `gh secret set GOOGLE_MAPS_EMBED_KEY --repo hikuzawa/japan-open-today` |
| `CF_WEB_ANALYTICS_TOKEN`（任意） | Cloudflare → Web Analytics → サイト追加 → スニペット内の token | `gh secret set CF_WEB_ANALYTICS_TOKEN --repo hikuzawa/japan-open-today` |

注意: `.env.example` には値を書かない（git にコミットされる）。値は `.env` だけに置く。

## レビュー待ち: 現在 0 件
S4 でいったん 5 件を保留にしたが、もう一段自動で探して全件の根拠が取れた（ADR 0009 追記）。
`data/review/kagawa.yaml` は空。**人の判断を待っている項目は無い。**

## 日次パイプライン（S7 で作成）
`.github/workflows/pipeline.yml` が毎日 05:00 JST（20:00 UTC）に
crawl → extract → 画像の用意 → build → 公開前検査 → Cloudflare Pages への配置を行う。
akiya-atlas は 03:00 JST なので実行時刻は重ならない。
`.github/workflows/checks.yml` は push / PR ごとに ruff・pytest・秘密の全履歴走査を行う。
手で流すときは `gh workflow run pipeline.yml --repo hikuzawa/japan-open-today`
（テンプレートだけを反映するなら `-f mode=deploy-only`）。

## AI 側で次に行う作業（人の作業を待たずに進められるもの）
- S1: sitemill の多言語・巡回ゲート・ページ種別の一般化
- S2: 営業時間・定休日のパーサ、祝日、「今日開いているか」の判定エンジン
- S3: 画像ライセンスの 4 経路を実装し、香川の対象施設で何件写真が付くか計測
- S4〜S5: 香川の施設・交通を 10 件ほど手で登録して縦断パイプラインを完走させる
- S6: 香川全域の自動発見とレビュー行列
