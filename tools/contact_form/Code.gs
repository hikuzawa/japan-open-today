/**
 * Japan Open Today お問い合わせフォーム（Google Apps Script）。
 *
 * akiya-atlas の同名の仕組みを土台にしている。違いは 2 つ:
 *   - 種別が別（correction / facility / question / partnership / spam）。掲載内容の誤りへの
 *     正しい対応は「隠す」ではなく「直す」なので、takedown（非表示）は持たない
 *   - **3 言語**（日本語・英語・繁体字）。設問は 3 言語併記で 1 つのフォームにし、
 *     各言語のページからは「返信の言語」を埋めた prefill 付きリンクで開く。
 *     自動返信はその言語で書く
 *
 * setup() を一度実行すると、次をまとめて行う:
 *   1. Google フォームの作成（お名前 / メールアドレス（返信先）/ 返信の言語 / 種別 / 内容）。
 *      回答者に Google ログインは求めない（メールの自動収集は使わず、返信は入力欄の値にだけ送る）
 *   2. 回答先スプレッドシートの作成とフォームへのリンク、「自動返信ログ」シートの用意
 *   3. フォーム送信時トリガー（onFormSubmit）の登録
 *   4. GitHub のラベル（correction / facility / needs-human）の用意（GITHUB_TOKEN 設定済みのとき）
 *   5. **言語ごとの prefill URL** をログに出力（i18n/<locale>.yaml の contact.url に貼る）
 *
 * 送信時の処理（AI による分類・自動返信・Issue 作成・ログ記録）は AutoReply.gs にある。
 * API キーと GitHub トークンはスクリプトプロパティにだけ置き、コードには書かない（README の表）。
 *
 * 再実行しても二重には作らない。作成済みの ID をスクリプトプロパティに保存し、無いものだけ作る。
 */

const CONFIG = {
  formTitle: 'Japan Open Today お問い合わせ / Contact / 聯絡我們',
  formDescription:
    '掲載内容の訂正、施設・事業者からのご連絡、その他のお問い合わせはこちらから。施設の予約・案内は行っていません。' +
    '送信後に自動返信メールが届きます（内容の分類と返信文の作成に AI を使います）。\n\n' +
    'Corrections to what we publish, messages from facilities and operators, and other enquiries. ' +
    'We do not book or arrange anything. You will receive an automatic reply (AI is used to ' +
    'classify your message and draft that reply).\n\n' +
    '刊載內容的訂正、設施與業者的聯絡，以及其他諮詢。本網站不提供預約或代辦服務。' +
    '送出後會收到自動回覆（分類與回覆文字使用 AI 產生）。',
  // 種別（選択肢）。3 言語併記。順番どおりに表示される
  categories: [
    '掲載内容の訂正 / Correction to what you publish / 刊載內容的訂正',
    '施設・事業者からの連絡 / From a facility or operator / 設施或業者的聯絡',
    '一般の質問 / General question / 一般諮詢',
    '取材・提携 / Press or partnership / 採訪與合作',
    'その他 / Other / 其他',
  ],
  // 返信の言語。各言語のページからは prefill でこの値が入った状態で開く
  languages: ['日本語', 'English', '繁體中文'],
  sheetName: 'Japan Open Today お問い合わせ（回答）',
  logSheetName: '自動返信ログ',
  notifyTo: '',
  confirmationMessage:
    'お問い合わせを受け付けました。まもなく自動返信メールが届きます。\n' +
    'Thank you. An automatic reply is on its way.\n' +
    '已收到您的訊息，稍後會寄出自動回覆。',
  siteBaseUrl: 'https://japan-open-today.com',
  // site.toml の [operator] name と合わせる（3 言語共通の 1 つの名前）
  operatorName: 'Japan Open Today',
  model: 'claude-haiku-4-5',
  replyLimitHours: 24,
  notifyOperator: true,
  githubLabels: {
    correction: {
      name: 'correction',
      color: 'B60205',
      description: '掲載内容の訂正の依頼（お問い合わせフォームから自動起票）',
    },
    facility: {
      name: 'facility',
      color: '0E8A16',
      description: '施設・事業者からのご連絡（お問い合わせフォームから自動起票）',
    },
    needsHuman: {
      name: 'needs-human',
      color: 'FBCA04',
      description: '人が判断する問い合わせ（お問い合わせフォームから自動起票）',
    },
  },
};

// スクリプトプロパティのキー。作成物の ID はコードが保存する。秘密情報と環境ごとの設定は人が入れる（README）
const PROP_FORM_ID = 'CONTACT_FORM_ID';
const PROP_SHEET_ID = 'CONTACT_SHEET_ID';
const PROP_ANTHROPIC_API_KEY = 'ANTHROPIC_API_KEY';
const PROP_GITHUB_TOKEN = 'GITHUB_TOKEN';
const PROP_GITHUB_REPO = 'GITHUB_REPO';
const PROP_NOTIFY_TO = 'NOTIFY_TO';
const PROP_SITE_BASE_URL = 'SITE_BASE_URL';
const PROP_OPERATOR_NAME = 'OPERATOR_NAME';
const PROP_MODEL = 'ANTHROPIC_MODEL';

const TRIGGER_HANDLER = 'onFormSubmit';
const Q_NAME = 'お名前 / Name / 姓名';
const Q_EMAIL = 'メールアドレス（返信先） / Email for our reply / 回覆用電子郵件';
const Q_LANG = '返信の言語 / Reply language / 回覆語言';
const Q_CATEGORY = '種別 / Category / 類別';
const Q_BODY = '内容 / Your message / 內容';

// 選択肢の文字列 → ロケール。AutoReply.gs が返信文の言語を決めるのに使う
const LOCALE_OF_CHOICE = { 日本語: 'ja', English: 'en', 繁體中文: 'zh-Hant' };

const LOG_HEADER = [
  '受付日時',
  'メール',
  'お名前',
  '言語',
  '選択した種別',
  'AI 分類',
  '確信度',
  '対応',
  '返信件名',
  '返信本文',
  'Issue URL',
  'モデル',
  'エラー',
  '判定理由',
  '要約',
];

/** すべてを一度に用意する。完了するとフォームの回答用 URL を返し、ログにも出す。 */
function setup() {
  const props = PropertiesService.getScriptProperties();
  const form = getOrCreateForm_(props);
  const ss = getOrCreateSheet_(props, form);
  ensureLogSheet_(ss);
  ensureSubmitTrigger_(form);
  const labels = ensureGithubLabels_();
  Logger.log('フォーム（回答用 URL）: %s', form.getPublishedUrl());
  Logger.log('フォーム（編集用 URL）: %s', form.getEditUrl());
  Logger.log('回答スプレッドシート: %s', ss.getUrl());
  Logger.log('送信時トリガー: %s（登録済み）', TRIGGER_HANDLER);
  Logger.log('GitHub ラベル: %s', labels);
  // 言語ごとの prefill URL。i18n/<locale>.yaml の contact.url に貼ると、各言語のページから
  // 「返信の言語」が埋まった状態でフォームが開く
  const prefilled = prefilledUrls_(form);
  Object.keys(prefilled).forEach((code) => Logger.log('prefill（%s）: %s', code, prefilled[code]));
  const missing = missingProperties_();
  if (missing.length) {
    Logger.log(
      '注意: スクリプトプロパティが未設定: %s。設定するまで AI 分類と自動返信は動かず、運営者への通知だけ行う',
      missing.join(', ')
    );
  }
  return form.getPublishedUrl();
}

/** 保存した ID を忘れる（フォームやシートは削除しない）。次の setup() で新しく作る。 */
function reset() {
  const props = PropertiesService.getScriptProperties();
  props.deleteProperty(PROP_FORM_ID);
  props.deleteProperty(PROP_SHEET_ID);
  Logger.log('保存していた ID を消しました。次の setup() で新しいフォームとシートを作ります。');
}

/** スクリプトプロパティと CONFIG をまとめた実行時設定。 */
function settings_() {
  const p = PropertiesService.getScriptProperties();
  return {
    apiKey: p.getProperty(PROP_ANTHROPIC_API_KEY) || '',
    githubToken: p.getProperty(PROP_GITHUB_TOKEN) || '',
    githubRepo: p.getProperty(PROP_GITHUB_REPO) || '',
    notifyTo:
      p.getProperty(PROP_NOTIFY_TO) || CONFIG.notifyTo || Session.getEffectiveUser().getEmail(),
    siteBaseUrl: (p.getProperty(PROP_SITE_BASE_URL) || CONFIG.siteBaseUrl).replace(/\/+$/, ''),
    operatorName: p.getProperty(PROP_OPERATOR_NAME) || CONFIG.operatorName,
    model: p.getProperty(PROP_MODEL) || CONFIG.model,
    sheetId: p.getProperty(PROP_SHEET_ID) || '',
  };
}

/** 必須のスクリプトプロパティのうち未設定のもの。 */
function missingProperties_() {
  const p = PropertiesService.getScriptProperties();
  return [PROP_ANTHROPIC_API_KEY, PROP_GITHUB_TOKEN, PROP_GITHUB_REPO].filter(
    (k) => !p.getProperty(k)
  );
}

function getOrCreateForm_(props) {
  const id = props.getProperty(PROP_FORM_ID);
  if (id) {
    try {
      return FormApp.openById(id);
    } catch (e) {
      Logger.log('保存されていたフォーム（%s）を開けないので作り直します: %s', id, e);
    }
  }
  const form = FormApp.create(CONFIG.formTitle);
  form.setDescription(CONFIG.formDescription);
  // メールアドレスの自動収集は使わない。回答者に Google ログインを求めず、返信先は入力欄の値だけを使う
  form.setLimitOneResponsePerUser(false);
  form.setAllowResponseEdits(false);
  form.setConfirmationMessage(CONFIG.confirmationMessage);

  form.addTextItem().setTitle(Q_NAME).setRequired(true);
  form
    .addTextItem()
    .setTitle(Q_EMAIL)
    .setHelpText('自動返信と回答をお送りします。/ We reply to this address. / 我們會回覆到這個信箱。')
    .setValidation(FormApp.createTextValidation().requireTextIsEmail().build())
    .setRequired(true);
  form
    .addMultipleChoiceItem()
    .setTitle(Q_LANG)
    .setChoiceValues(CONFIG.languages)
    .setRequired(true);
  form
    .addMultipleChoiceItem()
    .setTitle(Q_CATEGORY)
    .setChoiceValues(CONFIG.categories)
    .setRequired(true);
  form
    .addParagraphTextItem()
    .setTitle(Q_BODY)
    .setHelpText(
      '対象のページ URL があれば添えてください。個人情報や写真は書かないでください。/ ' +
        'Please include the page URL if there is one, and no personal data or photos. / ' +
        '若有相關網址請一併提供，請勿填寫個人資料或照片。'
    )
    .setRequired(true);

  props.setProperty(PROP_FORM_ID, form.getId());
  Logger.log('フォームを作成しました: %s', form.getId());
  return form;
}

/** 言語ごとに「返信の言語」を埋めた prefill URL を作る。 */
function prefilledUrls_(form) {
  const item = form
    .getItems(FormApp.ItemType.MULTIPLE_CHOICE)
    .filter((i) => i.getTitle() === Q_LANG)[0];
  const out = {};
  if (!item) return out;
  CONFIG.languages.forEach((choice) => {
    const response = form.createResponse();
    response.withItemResponse(item.asMultipleChoiceItem().createResponse(choice));
    out[LOCALE_OF_CHOICE[choice] || choice] = response.toPrefilledUrl();
  });
  return out;
}

function getOrCreateSheet_(props, form) {
  const id = props.getProperty(PROP_SHEET_ID);
  if (id) {
    try {
      return SpreadsheetApp.openById(id);
    } catch (e) {
      Logger.log('保存されていたスプレッドシート（%s）を開けないので作り直します: %s', id, e);
    }
  }
  const linked = currentDestinationId_(form);
  let ss;
  if (linked) {
    ss = SpreadsheetApp.openById(linked);
  } else {
    ss = SpreadsheetApp.create(CONFIG.sheetName);
    form.setDestination(FormApp.DestinationType.SPREADSHEET, ss.getId());
    Logger.log('回答スプレッドシートを作成してリンクしました: %s', ss.getId());
  }
  props.setProperty(PROP_SHEET_ID, ss.getId());
  return ss;
}

function currentDestinationId_(form) {
  try {
    return form.getDestinationId();
  } catch (e) {
    return null; // 回答先が未設定のときは例外になる
  }
}

/** 「自動返信ログ」シート。無ければ作り、見出し行を入れる。 */
function ensureLogSheet_(ss) {
  let sheet = ss.getSheetByName(CONFIG.logSheetName);
  if (!sheet) sheet = ss.insertSheet(CONFIG.logSheetName);
  if (sheet.getLastRow() === 0) {
    sheet.appendRow(LOG_HEADER);
    sheet.setFrozenRows(1);
  }
  return sheet;
}

function ensureSubmitTrigger_(form) {
  const exists = ScriptApp.getProjectTriggers().some(
    (t) =>
      t.getHandlerFunction() === TRIGGER_HANDLER &&
      t.getEventType() === ScriptApp.EventType.ON_FORM_SUBMIT &&
      t.getTriggerSourceId() === form.getId()
  );
  if (exists) return;
  ScriptApp.newTrigger(TRIGGER_HANDLER).forForm(form).onFormSubmit().create();
  Logger.log('送信時トリガーを登録しました: %s', TRIGGER_HANDLER);
}

/**
 * フォーム送信時に呼ばれる。回答は Google 側で回答スプレッドシートに自動記録される。
 * ここでは回答を取り出して AutoReply.gs の handleSubmission_ に渡す。
 */
function onFormSubmit(e) {
  const response = e.response;
  const answers = {};
  response.getItemResponses().forEach((ir) => {
    answers[ir.getItem().getTitle()] = String(ir.getResponse() || '');
  });
  const submission = {
    receivedAt: response.getTimestamp(),
    name: (answers[Q_NAME] || '').trim(),
    // 返信先は入力欄の値だけ（フォームのメールアドレス自動収集は使っていない）
    email: (answers[Q_EMAIL] || '').trim(),
    // prefill が効いていれば利用者は選ばずに済む。無ければ本文から推測する（AutoReply.gs）
    locale: LOCALE_OF_CHOICE[(answers[Q_LANG] || '').trim()] || '',
    selectedCategory: answers[Q_CATEGORY] || '',
    body: answers[Q_BODY] || '',
  };
  handleSubmission_(submission);
}
