## 这个 PR 做了什么

<!-- 一句话说清目的。不要写"修改了一些文件"。 -->

Closes #

## 所属模块

- [ ] **A** 采集与 ETL（`ingestion/`）
- [ ] **B** 检索与 RAG 核心（`rag-core/`）
- [ ] **C** Go API（`api-server/`）
- [ ] **D** Vue 3 前端（`frontend/`）
- [ ] **E** 评估体系（`eval/`）
- [ ] **F** 基础设施（`infra/`、`docker-compose.yml`、`.github/`）

> 一个 PR 只属于一个模块。跨模块改动请拆分。

## 是否修改了 API 契约或数据模型？

- [ ] **否** —— 只是实现，字段与端点未变
- [ ] **是** —— 已按 `CONTRIBUTING.md §4` 完成全部五步

如果是"是"，请逐项确认（缺一项不得合并）：

- [ ] 已更新 `docs/api.md`（人读的那份）
- [ ] 已同一天更新 `.claude/skills/sgu-notice-rag-dev/references/api-contract.md`
- [ ] 涉及字段时，已同步 `references/data-model.md` **与** `docs/data-model.md`
- [ ] 已跑 `bash .claude/skills/sgu-notice-rag-dev/scripts/check-contract.sh`，结果 exit 0
- [ ] 已取得交叉确认（改 HTTP：C↔D ／ 改 chunk 字段：A↔B ／ 改评估口径：B↔E）
- [ ] PR 标题以 `contract-change:` 开头

> 为什么查这么严：契约有两份副本（`docs/` 给人看、`references/` 给 Agent 看）。
> 只改一份，Agent 会按旧契约继续生成代码，六条链路会在集成日同时崩。

## 本地自测情况

请贴**真实输出**，不要只写"已测试"。

```
$ bash .claude/skills/sgu-notice-rag-dev/scripts/check-contract.sh
（粘贴结果）

$ （你的模块测试命令）
（粘贴结果）
```

- [ ] 契约自检通过
- [ ] 本模块测试全绿（新增功能已带测试）
- [ ] 没有提交 `.env`、密钥、模型权重、`node_modules/`、`dist/`
- [ ] 已 `git rebase main`，无冲突

## 自查清单

- [ ] 改动只落在本人负责的目录内（越界改动已在描述里说明理由并 @ 了对应负责人）
- [ ] 没有绕过采集的四道合规闸门（`ALLOWED_DOMAINS` / `REQUEST_DELAY_SECONDS` /
      `MAX_PAGES_PER_RUN` / `MAX_REQUESTS_PER_RUN`）
- [ ] 没有为绕过 403 提高并发、缩短延时或轮换 UA
- [ ] 新增的环境变量已写入 `.env.example` **与** `docs/deployment.md §4` 表格
- [ ] 新增/修改的配置项在 `README.md` 配置表里有对应说明

## Reviewer

| 角色 | 人 |
|---|---|
| 交叉 Reviewer（必填，按 `CONTRIBUTING.md` 的对照表） | @ |
| 契约变更确认人（仅契约变更时需要） | @ |

## 备注

<!-- 破坏性改动、后续 TODO、需要 Reviewer 重点看的地方。没有就删掉这节。 -->
