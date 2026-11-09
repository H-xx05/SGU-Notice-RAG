# ingestion（A 组 · 采集与 ETL）

把教务部「教学通知」栏目变成可检索的 chunk。**只做抓取、解析、时效抽取、切块、落盘**，
不碰向量库、不碰答案生成（那是 B 组的事）。

**附件只记链接、不下载正文**：xlsx/pdf 要额外解析与 OCR，而附件的时效性由正文条款决定，
先纳入会引入一堆无法验证的文本。要扩到附件时另开一轮，别在采集主链路里顺手加。

```bash
# 采集（默认 3 页，够验证）
python -m ingestion.cli crawl --out data/chunks.jsonl

# 校验产物是否符合契约（CI 用这个）
python -m ingestion.cli check data/chunks.jsonl

# 看数据分布，决定要不要人工抽查
python -m ingestion.cli stats data/chunks.jsonl
```

## ⚠️ 站点当前状态（2026-11 实测，必读）

`https://www.sgu.edu.cn/**` 对非浏览器访问统一返回 **HTTP 403 + 一个 `<title>block</title>`
的 WAF 拦截页**（约 47KB，里面一条通知都没有）。已验证：

| 试过的 | 结果 |
|---|---|
| 自定义 UA（带课程项目联系邮箱） | 403 |
| 完整浏览器 UA + Accept 头 | 403 |
| 首页 / 栏目页 / 列表页 | 全部 403 |

所以**本组代码目前无法跑真实抓取**。这不是 bug：代码把它识别成 `BlockedError`，
退出码 4，并打印处置指引，而不是伪装成"解析失败"让人去改解析器。

```
✗ 站点返回拦截页（HTTP 403 / WAF 拦截），本次采集已中止。
  这不是解析器的问题，改代码没有用。
  ...
  在明确授权之前，**不要**提高并发、缩短延时或反复重试。
```

**要做的**：按学校要求取得授权（校园网 / 教务系统导出 / 站点管理方放行）后再跑。
**不要做的**：降延时、加并发、换 IP、伪造 Referer —— 这些是把限流变成封禁的路径，
也不符合元提示词的采集合规约束。

在这期间，解析器、时效抽取、切块全部用**真实页面片段**做离线回归（`tests/`），
样本来自封锁生效前的抓取，结构与线上一致。

## 合规四闸门（`config.py`）

| 环境变量 | 默认 | 约束 |
|---|---|---|
| `SCRAPER_ALLOWED_DOMAINS` | `sgu.edu.cn` | 只能**收窄**：只接受默认白名单的子集 |
| `SCRAPER_REQUEST_DELAY_SECONDS` | `1.0` | 只能**调大**：小于 1.0 一律按 1.0 |
| `SCRAPER_MAX_PAGES_PER_RUN` | `50` | 只能**调小**。只算**列表页** |
| `SCRAPER_MAX_REQUESTS_PER_RUN` | `800` | 只能**调小**。算**所有**请求（含详情页） |

> 最后两个必须分开。踩过的坑：早期把 `MAX_PAGES_PER_RUN` 当成"每个 HTTP 请求都计数"，
> 结果默认 50 在 15 条/页的站上，翻完第 1 页（1 次列表 + 15 次详情）就把额度耗光，
> **后面几页的通知被静默丢弃**——只留一行 warning，数据莫名其妙变少。

所有 HTTP **必须**经 `Fetcher.get()`；绕过它四道闸门就形同虚设。CI 里有一条断言
`grep -rn "urlopen\|requests\." ingestion/` 只允许出现在 `fetcher.py`。

## 产物：`chunks.jsonl`

一行一个 chunk，字段见 `docs/data-model.md` §6 与 `.claude/skills/sgu-notice-rag-dev/references/data-model.md`。
`validate_record()` 是唯一校验入口。

**两种形态，别搞混**：

- `chunks.jsonl`：`effective_period` 是**嵌套对象** `{from, to, precision}`，带 `schema_version` / `content_hash` / `ingested_at`。这是契约产物。
- Qdrant payload（`ChunkRecord.to_payload()`）：`effective_from` / `effective_to` **扁平**。因为 Qdrant 的 range filter 只能作用于顶层字段，嵌套对象过滤不了。

转换只有一处（`emit/chunk_writer.py:to_payload`），读端用 `period_of()` 归一。
`cli check` 会先认形态：拿到 payload 形态的文件会提示"这不是 A 的产物"而不是报 20 条字段缺失。

## 目录

```
ingestion/
├─ config.py          闸门常量 + Settings（构造时把激进输入压回保守值）
├─ fetcher.py         唯一 HTTP 出口：白名单 / 限速 / 请求数闸门 / 拦截页识别 / 解码容错
├─ parsers/
│  ├─ list_page.py    列表页 → [ListItem]（分页倒序，读"下页"链接，绝不拼页号）
│  └─ detail_page.py  详情页 → 标题 / 时间 / 段落 / 附件**链接**（正文被拆成多个 span，必须拼回）
├─ temporal.py        时效抽取（只认显式表述，缺年份不猜）
├─ chunking/splitter.py  按小标题切块，每块带标题行
├─ doc_registry.py    doc_id / doc_version / topic_key / 版本链
├─ emit/chunk_writer.py  契约字段、幂等写盘、校验
├─ pipeline.py        Crawler：先读全再算版本，最后 emit
└─ cli.py             crawl / check / stats
```

## 三个"不要改"的行为

1. **分页是倒序的**：第 2 页是 `jxtz/96.htm`。必须读页面上的"下页"链接，拼 `page+1` 会抓错页。
2. **缺年份的日期不猜**：`请于11月6日前` 没有年份 → `effective_to = null`。猜成当年是时效性幻觉的来源。
3. **先读全再算版本**：`is_current` / `supersedes_doc_id` 依赖同批次其他文档，流式处理会算错。

## 测试

```bash
pytest tests -q        # 72 passed
ruff check ingestion tests
```

`tests/test_pipeline.py` 是离线端到端：用真实页面片段替换网络层，走完整
`Crawler.run()` → `ChunkWriter` → `validate_record` 链路。CI 不需要站点可访问。

