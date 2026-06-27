# Daily Brief Selection Logic and Database Mapping

Last updated: 2026-06-27

Command covered:

```powershell
python -m newsagent daily --output-language original --email
```

---

## 中文版

### 1. 总流程

该命令会完成：采集来源 -> 写入原始数据 -> 聚类成故事 -> 召回候选 -> 筛选简报内容 -> 生成 Markdown -> 可选发送邮件。

| 逻辑 | 生成结果 | 对应 DB 表 |
|---|---|---|
| 读取来源配置 | 当前启用的采集源快照 | `sources` |
| 运行级日志 | `run_started`、`collect_finished`、`briefing_created`、`email_finished`、`run_finished` | `pipeline_logs` |
| 每个来源的采集统计 | fetched、inserted、existing、failed/error | `source_collection_logs` |
| 原始新闻和行情入库 | 去重后的采集条目 | `raw_items` |
| 聚类和评分 | 可被简报选择的故事或市场品种 | `story_clusters` |
| 简报保存 | rules 版和 llm 版正文、所选 story id | `briefings` |
| LLM 调用记录 | 成功/失败、模型、错误信息 | `llm_runs` |
| 邮件发送记录 | 发送状态、主题、收件人数、错误 | `delivery_logs` |

### 2. 采集和入库

默认每个启用来源最多采集 40 条。普通新闻按 `URL + 标题 + 来源` 去重；市场行情按 `URL + 标题 + 来源 + quote_time + market_state` 去重。重复市场行情会刷新 `retrieved_at`，用于更新市场快照。

| 逻辑 | 生成结果 | 对应 DB 表和字段 |
|---|---|---|
| 来源注册/更新 | source id、名称、类型、分类、地区、优先级、enabled | `sources` |
| 原始条目保存 | 标题、URL、摘要、发布时间、采集时间、分类、tags、metrics | `raw_items` |
| 市场行情指标 | symbol、price、previous、change_pct、quote_time、market_state | `raw_items.metrics_json` |
| 每源采集结果 | fetched、inserted、existing、status、error | `source_collection_logs` |

### 3. 聚类

采集后，程序把新入库或被刷新过的 `raw_items` 聚合到 `story_clusters`。普通新闻按主题聚类；市场数据按市场品种聚类。

| 逻辑 | 生成结果 | 对应 DB 表和字段 |
|---|---|---|
| 找出待聚类条目 | 未聚类或 `retrieved_at > updated_at` 的 raw item | `raw_items`、`story_clusters.updated_at` |
| 生成故事 cluster | title、summary、category、region、score、source URLs、item IDs | `story_clusters` |
| 连接原始条目 | cluster 保存对应 raw item id | `story_clusters.item_ids_json` -> `raw_items.id` |

### 4. 候选召回

简报不会直接从全库选择，而是先召回几组候选：市场、主流国际新闻、中国/官方视频、医学健康、通用高分项。非市场新闻默认只保留 48 小时内内容；市场数据不受该限制。

| 逻辑 | 生成结果 | 对应 DB 表和字段 |
|---|---|---|
| 按类别召回市场 | 最新市场 clusters | `story_clusters.category = market` |
| 按关键词召回新闻 | 主流新闻、中国来源、医学健康等候选 | `story_clusters.title`、`summary`、`tags_json`、`source_urls_json` |
| 附加最新时间和 metrics | published_at、retrieved_at、metrics | `raw_items` 通过 `story_clusters.item_ids_json` 关联 |
| 用户反馈加权 | rank_score、feedback_boost 等内存字段 | `feedback` 参与查询时排序，不回写 `story_clusters.score` |

### 5. 评分原则

每条 raw item 入库后会计算基础 `score`，聚类后保存在 `story_clusters.score`。这个分数用于候选召回和部分排序，但最终 daily 简报还会叠加类别配额、地区配额和新鲜度规则。

| 评分项 | 加分/扣分原则 | 对应 DB 表和字段 |
|---|---|---|
| 来源优先级 | `P0 +40`、`P1 +20`、`P2 +5`，未知优先级约 +10 | `raw_items.priority`，来自 `sources.priority` |
| 类别权重 | market/policy +18，world_news +17，medicine/AI 类 +16，其他约 +8 | `raw_items.category` |
| 来源 tier | `max(0, 16 - tier * 4)`；tier 越低权重越高 | `raw_items.tier`，来自 `sources.tier` |
| 新鲜度 | 12 小时内 +18，24 小时内 +14，72 小时内 +10，7 天内 +5，更旧 +1，无时间 +2 | `raw_items.published_at`，缺失时用 `retrieved_at` |
| RSS/Feed 位置 | feed_rank 1 加 +14，2-3 加 +10，4-5 加 +7，6-10 加 +4 | `raw_items.metrics_json.feed_rank` |
| 高影响关键词 | 命中 AI、chip、war、sanction、market、oil、health、FDA、WHO、CGTN 等关键词，每个 +3，最多 +15 | title + summary |
| 信息完整度 | 有 URL +5，有 summary +3 | `raw_items.url`、`summary` |
| 用户反馈 | 直接反馈：important +30，track_more +24，show_less -24，irrelevant -45 | `feedback` |
| 类别反馈扩散 | 同类别：important +3，track_more +5，show_less -4，irrelevant -6 | `feedback` + `story_clusters.category` |
| 标签反馈扩散 | tag 重叠最多算 4 个；每个 tag：important +1.5，track_more +2.5，show_less -1.5，irrelevant -2.5 | `feedback` + `story_clusters.tags_json` |

候选召回阶段通常先按 `score DESC, updated_at DESC` 取出，再应用反馈形成临时 `rank_score`。`rank_score` 只在内存中使用，不会写回数据库。

### 6. 最终筛选

默认最多 65 条。当前配额大致是：市场约 32 条，主流国际新闻约 23 条，医学健康最多 5 条，AI/科技最多 5 条。

| 逻辑 | 生成结果 | 对应 DB 表 |
|---|---|---|
| 市场优先筛选 | 全球指数、商品外汇、美股行业、国际板块等 | 读取 `story_clusters` 和 `raw_items.metrics_json` |
| 主流新闻分地区筛选 | Europe、China、United States、Japan、South Korea 各最多 5 条 | 读取 `story_clusters.region` |
| 医学健康筛选 | 最多 5 条医学/健康内容 | 读取 `story_clusters.category`、`tags_json` |
| AI/科技筛选 | 最多 5 条 AI/技术内容 | 读取 `story_clusters.category`、`tags_json` |
| 最终入选 story id | 本次简报使用的 story id 列表 | 保存到 `briefings.story_ids_json` |

详细筛选标准：

1. 先把候选按 story id 去重，避免同一 cluster 重复进入。
2. market 内容先进入，但受市场配额限制，默认约 32 条。
3. market 内部先按品类排序：全球指数、商品/外汇、美股行业、国际板块、其他。
4. world_news 先按 `published_at` 或 `retrieved_at` 新鲜度排序，再看 `score`。
5. world_news 按地区分桶，Europe、China、United States、Japan、South Korea 每区最多 5 条。
6. medicine 和 AI/科技分别保留最多 5 条，优先选择时间最新的候选。
7. 如果还没达到 65 条，从剩余候选里按新鲜度和分数补齐。
8. 明显过旧或历史页面会被剔除，例如特定旧 China Daily/People 页面。
9. market 不走 48 小时过滤，因为休市时仍应展示最新常规交易收盘数据。

### 7. 简报输出

每次 daily 会生成两版：`rules` 和 `llm`。`output-language original` 不翻译正文，但会加入数据新鲜度区块。若 LLM 输出格式不合格、出现聊天式话术或引用未知 URL，会自动回退到规则版。

| 逻辑 | 生成结果 | 对应 DB 表和文件 |
|---|---|---|
| rules 版简报 | 稳定规则版正文 | `briefings.generation_mode = rules` |
| llm 版简报 | LLM 正文或 fallback_rules 正文 | `briefings.generation_mode = llm`、`generation_status` |
| 简报正文和证据 | body、canonical_body、story_ids_json | `briefings` |
| LLM 运行状态 | ok/error、provider、model | `llm_runs` |
| Markdown 输出 | latest.md、latest_rules.md、latest_llm.md、briefing 文件 | 文件系统 `data/outbox`，不是 DB 表 |

### 8. 邮件发送

带 `--email` 时，如果 `briefing.use_llm = true`，会发送 rules 和 llm 两封；如果为 false，只发送 rules 版。

| 逻辑 | 生成结果 | 对应 DB 表 |
|---|---|---|
| 单封邮件结果 | subject、briefing_id、ok、recipient_count、error | `delivery_logs.message` |
| 邮件汇总事件 | deliveries 列表和整体 ok | `pipeline_logs.event = email_finished` |

---

## 日本語版

### 1. 全体フロー

このコマンドは、ソース収集 -> raw 保存 -> ストーリークラスタ作成 -> 候補呼び出し -> ブリーフ内容選別 -> Markdown 生成 -> 必要に応じてメール送信、という順で動きます。

| ロジック | 生成結果 | 対応 DB テーブル |
|---|---|---|
| ソース設定の読み込み | 有効な収集ソースのスナップショット | `sources` |
| 実行ログ | `run_started`、`collect_finished`、`briefing_created`、`email_finished`、`run_finished` | `pipeline_logs` |
| ソース別収集統計 | fetched、inserted、existing、failed/error | `source_collection_logs` |
| raw 保存 | 重複排除済みの収集項目 | `raw_items` |
| クラスタリングとスコア | ブリーフ候補となるストーリーまたは市場銘柄 | `story_clusters` |
| ブリーフ保存 | rules 版、llm 版、選択 story id | `briefings` |
| LLM 実行記録 | 成功/失敗、モデル、エラー | `llm_runs` |
| メール送信記録 | 送信状態、件名、宛先数、エラー | `delivery_logs` |

### 2. 収集と保存

既定では、有効な各ソースから最大 40 件を収集します。通常ニュースは `URL + title + source`、市場データは `URL + title + source + quote_time + market_state` で重複判定します。重複した市場データでも `retrieved_at` は更新されます。

| ロジック | 生成結果 | 対応 DB テーブルとフィールド |
|---|---|---|
| ソース登録/更新 | source id、名前、種類、カテゴリ、地域、優先度、enabled | `sources` |
| raw 項目保存 | title、URL、summary、published_at、retrieved_at、category、tags、metrics | `raw_items` |
| 市場指標 | symbol、price、previous、change_pct、quote_time、market_state | `raw_items.metrics_json` |
| ソース別収集結果 | fetched、inserted、existing、status、error | `source_collection_logs` |

### 3. クラスタリング

収集後、新規または更新された `raw_items` を `story_clusters` に反映します。通常ニュースはテーマ単位、市場データは銘柄単位でまとまります。

| ロジック | 生成結果 | 対応 DB テーブルとフィールド |
|---|---|---|
| クラスタ対象の検出 | 未クラスタ、または `retrieved_at > updated_at` の raw item | `raw_items`、`story_clusters.updated_at` |
| story cluster 作成 | title、summary、category、region、score、source URLs、item IDs | `story_clusters` |
| raw item との関連 | cluster が参照する raw item id | `story_clusters.item_ids_json` -> `raw_items.id` |

### 4. 候補呼び出し

ブリーフは全 DB から直接選ばず、市場、主流国際ニュース、中国/公式動画、医療健康、汎用高スコア項目を候補として呼び出します。市場以外は既定で 48 時間以内に絞ります。市場データは休場時も最新終値が必要なため対象外です。

| ロジック | 生成結果 | 対応 DB テーブルとフィールド |
|---|---|---|
| 市場カテゴリ呼び出し | 最新市場 clusters | `story_clusters.category = market` |
| キーワード呼び出し | 主流ニュース、中国ソース、医療健康など | `story_clusters.title`、`summary`、`tags_json`、`source_urls_json` |
| 最新時刻と metrics の付与 | published_at、retrieved_at、metrics | `raw_items`、`story_clusters.item_ids_json` |
| フィードバック加重 | rank_score、feedback_boost などの一時値 | `feedback`、ただし `story_clusters.score` には書き戻さない |

### 5. スコアリング原則

各 raw item は保存後に基礎 `score` を計算します。クラスタリング後、この値は `story_clusters.score` に保存されます。daily では、このスコアに加えて、カテゴリ枠、地域枠、鮮度ルールを使います。

| スコア項目 | 加点/減点ルール | 対応 DB テーブルとフィールド |
|---|---|---|
| ソース優先度 | `P0 +40`、`P1 +20`、`P2 +5`、未知の場合はおおむね +10 | `raw_items.priority`、元は `sources.priority` |
| カテゴリ重み | market/policy +18、world_news +17、medicine/AI 系 +16、その他は約 +8 | `raw_items.category` |
| ソース tier | `max(0, 16 - tier * 4)`。tier が低いほど高評価 | `raw_items.tier`、元は `sources.tier` |
| 鮮度 | 12 時間以内 +18、24 時間以内 +14、72 時間以内 +10、7 日以内 +5、それ以上 +1、時刻なし +2 | `raw_items.published_at`、なければ `retrieved_at` |
| RSS/Feed 位置 | feed_rank 1 は +14、2-3 は +10、4-5 は +7、6-10 は +4 | `raw_items.metrics_json.feed_rank` |
| 高影響キーワード | AI、chip、war、sanction、market、oil、health、FDA、WHO、CGTN など。1 件 +3、最大 +15 | title + summary |
| 情報の完全性 | URL あり +5、summary あり +3 | `raw_items.url`、`summary` |
| 直接フィードバック | important +30、track_more +24、show_less -24、irrelevant -45 | `feedback` |
| カテゴリへの波及 | 同カテゴリに important +3、track_more +5、show_less -4、irrelevant -6 | `feedback` + `story_clusters.category` |
| タグへの波及 | 重複 tag は最大 4 個まで。1 tag あたり important +1.5、track_more +2.5、show_less -1.5、irrelevant -2.5 | `feedback` + `story_clusters.tags_json` |

候補呼び出しでは、基本的に `score DESC, updated_at DESC` で取得し、その後フィードバックを反映した一時的な `rank_score` を使います。`rank_score` は DB に保存されません。

### 6. 最終選別

既定では最大 65 件です。目安は、市場約 32 件、主流国際ニュース約 23 件、医療健康最大 5 件、AI/技術最大 5 件です。

| ロジック | 生成結果 | 対応 DB テーブル |
|---|---|---|
| 市場優先選別 | グローバル指数、商品/為替、米国セクター、国際セクターなど | `story_clusters`、`raw_items.metrics_json` |
| 地域別ニュース選別 | Europe、China、United States、Japan、South Korea 各最大 5 件 | `story_clusters.region` |
| 医療健康選別 | 最大 5 件 | `story_clusters.category`、`tags_json` |
| AI/技術選別 | 最大 5 件 | `story_clusters.category`、`tags_json` |
| 採用 story id | 今回のブリーフで使う story id 一覧 | `briefings.story_ids_json` |

詳細な選別基準：

1. story id で候補を重複排除する。
2. market を先に入れるが、市場枠の上限を超えない。既定では約 32 件。
3. market の中では、グローバル指数、商品/為替、米国セクター、国際セクター、その他の順に並べる。
4. world_news は `published_at` または `retrieved_at` の鮮度を優先し、次に `score` を見る。
5. world_news は地域別に分け、Europe、China、United States、Japan、South Korea それぞれ最大 5 件。
6. medicine と AI/技術は、それぞれ最大 5 件で、鮮度の高い候補を優先する。
7. 65 件に満たない場合は、残り候補から鮮度とスコアで補完する。
8. 明らかに古い履歴ページは除外する。例：一部の古い China Daily/People ページ。
9. market は 48 時間フィルタの対象外。休場時も最新の通常取引終値を表示するため。

### 7. ブリーフ出力

daily は毎回 `rules` と `llm` の 2 版を生成します。`output-language original` では本文を翻訳せず、データ鮮度セクションだけ追加します。LLM 出力が形式不正、チャット風、未知 URL 引用の場合は rules 版へフォールバックします。

| ロジック | 生成結果 | 対応 DB テーブルとファイル |
|---|---|---|
| rules 版 | ルールベースの安定した本文 | `briefings.generation_mode = rules` |
| llm 版 | LLM 本文または fallback_rules 本文 | `briefings.generation_mode = llm`、`generation_status` |
| ブリーフ本文と証拠 | body、canonical_body、story_ids_json | `briefings` |
| LLM 実行状態 | ok/error、provider、model | `llm_runs` |
| Markdown 出力 | latest.md、latest_rules.md、latest_llm.md、briefing files | ファイルシステム `data/outbox`、DB ではない |

### 8. メール送信

`--email` が付く場合、`briefing.use_llm = true` なら rules と llm の 2 通を送ります。false なら rules 版のみです。

| ロジック | 生成結果 | 対応 DB テーブル |
|---|---|---|
| 個別メール結果 | subject、briefing_id、ok、recipient_count、error | `delivery_logs.message` |
| メール集計イベント | deliveries 一覧と全体 ok | `pipeline_logs.event = email_finished` |

---

## English Version

### 1. Overall Flow

This command runs source collection -> raw storage -> story clustering -> candidate retrieval -> briefing selection -> Markdown generation -> optional email delivery.

| Logic | Generated Result | DB Table |
|---|---|---|
| Load source config | Snapshot of enabled collection sources | `sources` |
| Run-level logs | `run_started`, `collect_finished`, `briefing_created`, `email_finished`, `run_finished` | `pipeline_logs` |
| Per-source collection stats | fetched, inserted, existing, failed/error | `source_collection_logs` |
| Raw news and quote storage | Deduplicated collected items | `raw_items` |
| Clustering and scoring | Story or market clusters eligible for briefing | `story_clusters` |
| Briefing persistence | rules edition, llm edition, selected story ids | `briefings` |
| LLM telemetry | success/failure, model, error | `llm_runs` |
| Email delivery records | status, subject, recipient count, error | `delivery_logs` |

### 2. Collection and Storage

By default, each enabled source collects up to 40 items. Regular news is deduplicated by `URL + title + source`; market quotes use `URL + title + source + quote_time + market_state`. Duplicate market quotes refresh `retrieved_at` so market snapshots can be corrected.

| Logic | Generated Result | DB Table and Fields |
|---|---|---|
| Source upsert | source id, name, kind, category, region, priority, enabled | `sources` |
| Raw item storage | title, URL, summary, published_at, retrieved_at, category, tags, metrics | `raw_items` |
| Market metrics | symbol, price, previous, change_pct, quote_time, market_state | `raw_items.metrics_json` |
| Per-source result | fetched, inserted, existing, status, error | `source_collection_logs` |

### 3. Clustering

After collection, new or refreshed `raw_items` are applied to `story_clusters`. Regular news clusters by topic; market data clusters by instrument.

| Logic | Generated Result | DB Table and Fields |
|---|---|---|
| Detect items to cluster | Unclustered raw items or `retrieved_at > updated_at` | `raw_items`, `story_clusters.updated_at` |
| Create story cluster | title, summary, category, region, score, source URLs, item IDs | `story_clusters` |
| Link to raw items | Raw item ids referenced by the cluster | `story_clusters.item_ids_json` -> `raw_items.id` |

### 4. Candidate Retrieval

The brief is not selected directly from the whole database. It first retrieves candidate groups: market, mainstream international news, China/official video sources, medical and health, and general high-score items. Non-market items use a 48-hour lookback by default. Market items are exempt because closed markets still need the latest close.

| Logic | Generated Result | DB Table and Fields |
|---|---|---|
| Retrieve market category | Latest market clusters | `story_clusters.category = market` |
| Retrieve by keyword | Mainstream, China, medical, and other candidates | `story_clusters.title`, `summary`, `tags_json`, `source_urls_json` |
| Attach latest metadata | published_at, retrieved_at, metrics | `raw_items` via `story_clusters.item_ids_json` |
| Apply feedback weighting | In-memory rank_score and feedback_boost | `feedback`; not written back to `story_clusters.score` |

### 5. Scoring Principles

Each raw item receives a base `score` after insertion. After clustering, this value is stored on `story_clusters.score`. The daily pipeline then combines that score with category quotas, region quotas, and freshness rules.

| Scoring Factor | Weighting Rule | DB Table and Fields |
|---|---|---|
| Source priority | `P0 +40`, `P1 +20`, `P2 +5`; unknown priority is about +10 | `raw_items.priority`, derived from `sources.priority` |
| Category weight | market/policy +18, world_news +17, medicine/AI classes +16, other about +8 | `raw_items.category` |
| Source tier | `max(0, 16 - tier * 4)`; lower tier means higher score | `raw_items.tier`, derived from `sources.tier` |
| Freshness | within 12h +18, 24h +14, 72h +10, 7d +5, older +1, missing time +2 | `raw_items.published_at`, falling back to `retrieved_at` |
| RSS/feed position | feed_rank 1 gets +14, 2-3 +10, 4-5 +7, 6-10 +4 | `raw_items.metrics_json.feed_rank` |
| High-impact keywords | AI, chip, war, sanction, market, oil, health, FDA, WHO, CGTN, etc.; +3 each, capped at +15 | title + summary |
| Completeness | URL +5, summary +3 | `raw_items.url`, `summary` |
| Direct feedback | important +30, track_more +24, show_less -24, irrelevant -45 | `feedback` |
| Category feedback spread | Same category: important +3, track_more +5, show_less -4, irrelevant -6 | `feedback` + `story_clusters.category` |
| Tag feedback spread | Up to 4 overlapping tags; per tag: important +1.5, track_more +2.5, show_less -1.5, irrelevant -2.5 | `feedback` + `story_clusters.tags_json` |

Candidate retrieval generally starts with `score DESC, updated_at DESC`, then applies feedback to create an in-memory `rank_score`. `rank_score` is not persisted to the database.

### 6. Final Selection

The default maximum is 65 items. Approximate quotas are: market about 32, mainstream international news about 23, medical and health up to 5, AI/technology up to 5.

| Logic | Generated Result | DB Table |
|---|---|---|
| Market-first selection | Global indices, commodities/FX, U.S. sectors, international sectors | `story_clusters`, `raw_items.metrics_json` |
| Regional news selection | Up to 5 each for Europe, China, United States, Japan, South Korea | `story_clusters.region` |
| Medical and health selection | Up to 5 items | `story_clusters.category`, `tags_json` |
| AI/technology selection | Up to 5 items | `story_clusters.category`, `tags_json` |
| Selected story ids | Story ids used by this briefing | `briefings.story_ids_json` |

Detailed selection criteria:

1. Deduplicate candidates by story id.
2. Add market items first, up to the market quota, about 32 by default.
3. Within market, use this category order: global indices, commodities/FX, U.S. sectors, international sectors, other.
4. For world_news, sort first by `published_at` or `retrieved_at` freshness, then by `score`.
5. Split world_news by region, with up to 5 each for Europe, China, United States, Japan, and South Korea.
6. Add medicine and AI/technology items separately, each up to 5, preferring fresher candidates.
7. If fewer than 65 items have been selected, fill from remaining candidates by freshness and score.
8. Drop obviously stale historical pages, such as specific old China Daily/People pages.
9. Exempt market from the 48-hour filter because closed markets still need the latest regular-session close.

### 7. Brief Output

Each daily run creates both `rules` and `llm` editions. With `output-language original`, the body is not translated, but a data freshness section is added. If the LLM output is malformed, chat-like, or cites unknown URLs, it falls back to the rules edition.

| Logic | Generated Result | DB Table and Files |
|---|---|---|
| rules edition | Stable deterministic body | `briefings.generation_mode = rules` |
| llm edition | LLM body or fallback_rules body | `briefings.generation_mode = llm`, `generation_status` |
| Brief body and evidence | body, canonical_body, story_ids_json | `briefings` |
| LLM run status | ok/error, provider, model | `llm_runs` |
| Markdown output | latest.md, latest_rules.md, latest_llm.md, briefing files | File system `data/outbox`, not a DB table |

### 8. Email Delivery

With `--email`, if `briefing.use_llm = true`, both rules and llm messages are sent. If false, only the rules edition is sent.

| Logic | Generated Result | DB Table |
|---|---|---|
| Individual email result | subject, briefing_id, ok, recipient_count, error | `delivery_logs.message` |
| Email summary event | deliveries list and aggregate ok | `pipeline_logs.event = email_finished` |
