-- 【A/C/E 共用】Postgres 初始化 schema
-- 与 docs/data-model.md §2 逐字段一致；改字段 = 契约变更（SKILL.md §1）

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ---------- notices：文档级，一行一个版本 ----------
CREATE TABLE IF NOT EXISTS notices (
  doc_id              TEXT PRIMARY KEY,
  title               TEXT NOT NULL,
  publish_date        DATE NOT NULL,
  effective_from      DATE,
  effective_to        DATE,
  effective_precision TEXT NOT NULL DEFAULT 'unknown'
                      CHECK (effective_precision IN ('day','month','term','unknown')),
  doc_version         TEXT NOT NULL,
  doc_version_rank    INTEGER NOT NULL,
  is_current          BOOLEAN NOT NULL DEFAULT TRUE,
  supersedes_doc_id   TEXT REFERENCES notices(doc_id),
  content_type        TEXT NOT NULL
                      CHECK (content_type IN ('notice_body','attachment','table','appendix')),
  source_url          TEXT NOT NULL,
  topic_key           TEXT,
  raw_html_path       TEXT,
  ingested_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT chk_effective_order
    CHECK (effective_to IS NULL OR effective_from IS NULL OR effective_to >= effective_from)
);

CREATE INDEX IF NOT EXISTS idx_notices_publish   ON notices (publish_date DESC);
CREATE INDEX IF NOT EXISTS idx_notices_current   ON notices (is_current, publish_date DESC);
CREATE INDEX IF NOT EXISTS idx_notices_topic     ON notices (topic_key, doc_version_rank DESC);
CREATE INDEX IF NOT EXISTS idx_notices_effective ON notices (effective_from, effective_to);
CREATE INDEX IF NOT EXISTS idx_notices_title_trgm ON notices USING gin (title gin_trgm_ops);

-- ---------- chunks ----------
CREATE TABLE IF NOT EXISTS chunks (
  chunk_id     TEXT PRIMARY KEY,
  doc_id       TEXT NOT NULL REFERENCES notices(doc_id) ON DELETE CASCADE,
  seq          INTEGER NOT NULL,
  section_path TEXT NOT NULL,
  content_type TEXT NOT NULL,
  text         TEXT NOT NULL,
  token_count  INTEGER NOT NULL,
  content_hash TEXT NOT NULL,
  CONSTRAINT uq_chunk_seq UNIQUE (doc_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_chunks_doc  ON chunks (doc_id);
CREATE INDEX IF NOT EXISTS idx_chunks_hash ON chunks (content_hash);

-- ---------- eval ----------
CREATE TABLE IF NOT EXISTS eval_runs (
  run_id       TEXT PRIMARY KEY,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  dataset_size INTEGER NOT NULL,
  git_sha      TEXT,
  rag_config   JSONB NOT NULL DEFAULT '{}'::jsonb,
  metrics      JSONB NOT NULL DEFAULT '{}'::jsonb,
  -- by_year / regressions 是 /api/v1/eval/summary 响应体的两段。
  -- 为什么不从 eval_results 现算：报告一旦发出就是历史事实，
  -- 事后补的数据或改的口径会让"当时跑出 0.08"变成"现在算出来 0.12"，看板失去可信度。
  -- 存快照 = 每次跑批的结果可复现、可对比。
  by_year      JSONB NOT NULL DEFAULT '[]'::jsonb,
  regressions  JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE TABLE IF NOT EXISTS eval_results (
  run_id    TEXT NOT NULL REFERENCES eval_runs(run_id) ON DELETE CASCADE,
  query_id  TEXT NOT NULL,
  answer    TEXT,
  refused   BOOLEAN NOT NULL,
  metrics   JSONB NOT NULL DEFAULT '{}'::jsonb,
  stale_hit BOOLEAN NOT NULL DEFAULT FALSE,
  citations JSONB,
  -- 下面两列是**可选补充字段**（契约允许"新增字段必须可选且向后兼容"）：
  -- 没有它们，C 的 /api/v1/eval/summary 无法按年聚合 by_year —— as_of 是评估时点，
  -- 缺了它只能拿 created_at 当年份，历史时点题（q02 用 as_of=2025-06-01）会被算进 2026 年。
  category  TEXT,
  as_of     DATE,
  PRIMARY KEY (run_id, query_id)
);

CREATE INDEX IF NOT EXISTS idx_eval_results_stale ON eval_results (run_id, stale_hit);
CREATE INDEX IF NOT EXISTS idx_eval_results_year  ON eval_results (run_id, as_of);
