# data-model.md — chunk schema 与时间过滤（只读引用）

> 摘要，完整版在 `docs/data-model.md`。字段名不一致时以本文件为准。
> 本文件只回答三件事：**chunk 长什么样**、**时间过滤按什么顺序生效**、**字段怎么演进**。

## 1. 存储分工

| 存储 | 存什么 | 归属 |
|---|---|---|
| Postgres `notices` | 通知文档级元数据（一行一个版本） | C 读，A 写 |
| Postgres `chunks` | chunk 正文与元数据（与 Qdrant payload 同源） | A 写，C 只读统计 |
| Postgres `eval_runs` / `eval_results` | 评估批次与逐题结果 | E 写，C 读 |
| Qdrant `sgu_notices` | 向量 + payload（payload = chunk 元数据子集） | B 读写 |

> 单一事实源：chunk 元数据先由 A 落地 `chunks.jsonl` → 入 Postgres + Qdrant。**两侧字段必须一致**，不一致以 `chunks.jsonl` 为准。

## 2. `chunks.jsonl` 字段（一行一个 chunk）

| 字段 | 类型 | 必填 | 规则 / 约束 |
|---|---|---|---|
| `chunk_id` | string | 是 | `{doc_id}#c{NNN}`，全局唯一，稳定 |
| `doc_id` | string | 是 | `jwb-jxtz-{发布年}-{站点 news_id}`（如 `jwb-jxtz-2026-29459`，宽度按实际位数）；**不同版本不同 doc_id**。用站点 id 而非自增序号，重跑才幂等 |
| `title` | string | 是 | 原文标题 |
| `publish_date` | string | 是 | `YYYY-MM-DD`；解析失败用列表页日期 |
| `effective_period` | object | 是 | `{from,to,precision}`，不得省略 |
| `effective_period.from` | string\|null | 是（可 null） | |
| `effective_period.to` | string\|null | 是（可 null） | `null` = 未规定结束 |
| `effective_period.precision` | string | 是 | `day`\|`month`\|`term`\|`unknown` |
| `doc_version` | string | 是 | `v{year}.{n}` |
| `doc_version_rank` | int | 是 | `year*100+n`，用于比较新旧 |
| `section_path` | string | 是 | `正文 > 一、报名安排` / `附件 > 计划表.xlsx` / `正文` |
| `content_type` | string | 是 | `notice_body`\|`attachment`\|`table`\|`appendix` |
| `source_url` | string | 是 | 绝对 URL，域名必须 ∈ `ALLOWED_DOMAINS` |
| `text` | string | 是 | 320–800 tokens，首行含标题 |
| `token_count` | int | 是 | bge-m3 tokenizer 估算 |
| `content_hash` | string | 是 | `sha256(text)`，幂等 upsert |
| `supersedes_doc_id` | string\|null | 否 | 仅显式替代关系，禁止猜测 |
| `is_current` | bool | 是 | 默认 `true` |
| `ingested_at` | string | 是 | RFC3339 |

### Qdrant payload 子集（B 必须能容忍这些字段缺失）

```
chunk_id doc_id title publish_date effective_from effective_to
doc_version doc_version_rank section_path content_type source_url
is_current supersedes_doc_id
```

> 注意：Qdrant payload 把 `effective_period` 拆成 `effective_from`/`effective_to` 两个顶层字段（便于 range filter），**这是唯一的字段形态差异**，转换由 A 在 `ingestion/emit/chunk_writer.py` 完成，B 不再转换。

## 3. Postgres 表

```sql
-- notices：文档级（一行一个版本）
CREATE TABLE notices (
  doc_id           TEXT PRIMARY KEY,
  title            TEXT NOT NULL,
  publish_date     DATE NOT NULL,
  effective_from   DATE,
  effective_to     DATE,
  effective_precision TEXT NOT NULL DEFAULT 'unknown',
  doc_version      TEXT NOT NULL,
  doc_version_rank INTEGER NOT NULL,
  is_current       BOOLEAN NOT NULL DEFAULT TRUE,
  supersedes_doc_id TEXT REFERENCES notices(doc_id),
  content_type     TEXT NOT NULL,
  source_url       TEXT NOT NULL,
  raw_html_path    TEXT,
  ingested_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_notices_publish ON notices (publish_date DESC);
CREATE INDEX idx_notices_current ON notices (is_current, publish_date DESC);

-- chunks：与 chunks.jsonl 同字段
CREATE TABLE chunks (
  chunk_id      TEXT PRIMARY KEY,
  doc_id        TEXT NOT NULL REFERENCES notices(doc_id) ON DELETE CASCADE,
  section_path  TEXT NOT NULL,
  content_type  TEXT NOT NULL,
  text          TEXT NOT NULL,
  token_count   INTEGER NOT NULL,
  content_hash  TEXT NOT NULL,
  seq           INTEGER NOT NULL,
  UNIQUE (doc_id, seq)
);

-- eval_runs / eval_results
CREATE TABLE eval_runs (
  run_id     TEXT PRIMARY KEY,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  dataset_size INTEGER NOT NULL,
  metrics    JSONB NOT NULL,
  by_year    JSONB NOT NULL DEFAULT '[]'::jsonb,   -- /eval/summary 快照
  regressions JSONB NOT NULL DEFAULT '[]'::jsonb   -- /eval/summary 快照
);
CREATE TABLE eval_results (
  run_id    TEXT NOT NULL REFERENCES eval_runs(run_id) ON DELETE CASCADE,
  query_id  TEXT NOT NULL,
  answer    TEXT,
  refused   BOOLEAN NOT NULL,
  metrics   JSONB NOT NULL,
  stale_hit BOOLEAN NOT NULL DEFAULT FALSE,
  category  TEXT,        -- 可选
  as_of     DATE,        -- 可选：by_year 按它分组
  PRIMARY KEY (run_id, query_id)
);
```

## 4. 时间过滤生效顺序（硬约束）

```
query
  └─(1) 时间意图识别 time_filter.py
         intent ∈ { current, historical, deadline, none }
         产出 as_of（默认今天）、date_from/date_to
  └─(2) 硬过滤（Qdrant payload filter，检索之前）
         publish_date <= as_of
         AND (effective_to IS NULL OR effective_to >= as_of)
         mode=off 时跳过本步（仅评估）
  └─(3) 语义召回 dense top40 + sparse top40
  └─(4) RRF 融合（Qdrant 内置，k=2）
  └─(5) Rerank top20 → top8
  └─(6) 过期护栏：intent=current 时对 is_current=false 降权 0.5 或剔除
  └─(7) 生成 / 拒答
```

**为什么过滤在前**：先过滤再召回，避免"召回被旧版本占满后无法补救"；先召回再过滤会显著抬高 `stale_recall_rate`（E 的对照实验口径见 `eval/README.md`）。

## 5. 判定规则（写入 ingestion，B 只读）

1. `publish_date` = 列表页日期；详情页有更精确日期时以详情页为准。
2. `effective_period` 提取优先级：正文"自…至…" > "截至…" > "…前" > 未提及（`unknown`）。
3. `is_current`：同主题且有更新版本**且新版正文显式提及替代/废止** → 旧版本 `false` 且新版 `supersedes_doc_id` 指向旧版；仅靠标题相似**不得**判定。
4. `doc_version_rank` 只在同年内递增；跨年直接由年份决定新旧。

## 6. 字段演进规则

- 新增字段：**可选**，读端容忍缺失；写端立即写入。
- 改名/改类型/删除：属于契约变更，走 `SKILL.md §1` 三步流程 + E 全量回归。
- 版本号：schema 变更时同步 bump `chunks.jsonl` 的 `schema_version`（写入每行，初期为 `"1.0"`）。
