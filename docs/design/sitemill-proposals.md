# sitemill に置きたいもの（案B の共通部品）

案B（ADR 0013）で japan-open-today に作った表示の部品のうち、**akiya-atlas でも同じ形で使うもの**。
いまはこのサービスに置いてある。エンジンに寄せるときはこの一覧を使う。担当外のセッションは実装しない。

書いた日: 2026-09-20

## 1. ヒーロー帯＋2 カラム＋sticky 右列

- いまの実装: `static/style.css` の `.hero` / `.hero-inner` / `.layout` / `.col-main` / `.col-side`、
  `templates/base.html`（`<main>` は素のまま、各ページが `.container` を持つ）
- 何をする部品か: 全幅の色帯（主題と判定）＋その下の 2 カラム。1024px 以上で右列が sticky、
  スマホでは右列（写真・主ボタン・地図）が本文より前に来る
- なぜエンジン向きか: サービス固有の事実は中身だけで、骨格はどちらのサイトでも同じ。
  akiya-atlas の物件ページも「主題＋判定＋一次情報への主導線＋補助情報」の形
- 移すときの注意: `<main>` から `.container` を外す変更は全テンプレートに及ぶ。
  lastmod の指紋はテンプレートの変更で全ページ動くので、台帳の作り直し（sitemill v0.7.4）が要る

## 2. 言語ボタン（`<details>` の自称メニュー）

- いまの実装: `templates/partials/ui.html` の `lang_switch(locales, locale, meta)` と、CSS の `.lang` / `.lang-menu`
- 何をする部品か: 地球アイコン＋現在の言語名＋▾。押すと一覧が開き、各言語は自称で並ぶ。
  現在の言語に `aria-current` と ✓。**追加の JS を使わない**
- なぜエンジン向きか: 材料は `locales`（`LocaleConfig.label`）と `meta.alternates` だけで、サービス固有の
  データを使わない。言語が増えても見た目が変わらない形は、全国展開でも同じ要求になる
- 移すときの注意: 文言キー `nav.language`（アクセシブル名）をエンジンの既定カタログに持たせるか、
  サービス側の必須キーにするかを決める

## 3. 判定の判子（`verdict_stamp`）

- いまの実装: `templates/partials/ui.html` の `verdict_stamp(verdict, locale)`、CSS の `.stamp` 系
- 何をする部品か: 丸い判子（`state.*_short`）＋日付＋状態＋時間帯。**色だけに意味を持たせず**、
  必ず状態の文字を添える。語が長い言語（Open）は小さい文字に切り替える
- なぜエンジン向きか: `openstatus` の判定（open / closed / unknown と根拠）はエンジンの型で、
  その表示の作法（日付を明記する・色に頼らない・根拠を添える）も ADR 0004 でエンジン側の決めごと
- 移すときの注意: akiya-atlas の「募集中／募集終了／不明」も同じ 3 値なので、状態名を
  サービスから渡せる形（`state_key` を引数にする）にする

## 4. 下部固定バー（スマホだけの主導線）

- いまの実装: `templates/base.html` の `bottom_bar` ブロックと CSS の `.bottom-bar`
- 何をする部品か: スマホで一次情報への主ボタンを画面から外さない。1024px 以上では消す。
  `body:has(.bottom-bar) .site-footer` でフッタに余白を足す
- なぜエンジン向きか: 「一次情報へのリンクを常に主導線に置く」は ADR 0007 の決めごとで、
  どのサービスでも同じ
- 移すときの注意: 広告の枠を固定バーに入れない（ADR 0012 の掲載場所の宣言と食い違う）

## 5. 画面の上に出る写真は先に読む（`photo_figure` の優先度）

- いまの実装: `templates/partials/ui.html` の `photo_figure_eager(asset, locale, src)`
  （sitemill の `photo_figure` の写しに `loading="eager" fetchpriority="high"` を足したもの）
- なぜエンジン向きか: `sitemill/macros.html` の `photo_figure` は常に `loading="lazy"` で、
  画面の上に出る 1 枚（= LCP）にもそれが当たる。Lighthouse は「LCP の画像を lazy にしない」
  「fetchpriority=high を付ける」と指摘し、本番の施設ページは LCP 6.0 秒だった
- 移すときの形: `photo_figure(..., priority=false)` の引数を足し、true のときだけ
  `loading="eager" fetchpriority="high"` にする。既定は今までどおり lazy
