# 贡献规范（CONTRIBUTING）

本文件是流程约束，不是建议。**代码规范**与**接口契约**以
[`.claude/skills/sgu-notice-rag-dev/SKILL.md`](.claude/skills/sgu-notice-rag-dev/SKILL.md) 为准，
两者冲突时以 Skill 为准。

---

## 1. 铁律

> **禁止直接 push 到 `main`。**
> 所有改动必须走分支 → PR → Reviewer 合并。`main` 会被设为 Protected Branch，
> 直推会被 GitHub 直接拒绝——这不是配置故障，是设计如此。

原因：六条链路（采集 / 检索 / API / 前端 / 评估 / 基建）并行开发，
`main` 一旦被半成品污染，六个人会同时红，而排查成本是单人问题的六倍。

---

## 2. 分支命名

格式固定为 `feat/<成员字母>-<模块名>`，CI、PR 模板与任务看板都按这个认人。

| 成员 | 分支 | 负责目录 |
|---|---|---|
| A | `feat/A-scraper` | `ingestion/` |
| B | `feat/B-rag-core` | `rag-core/` |
| C | `feat/C-api` | `api-server/` |
| D | `feat/D-frontend` | `frontend/` |
| E | `feat/E-eval` | `eval/` |
| F | `feat/F-infra` | `infra/`、`docker-compose.yml`、`.github/` |

约定：

- 一个分支只做一件事。跨两个模块的改动拆成两个 PR，即使它们互相依赖。
- 分支名里的字母必须是**本人**。代别人提 PR 时用对方的分支，不要新建一个 `feat/C-api-by-A`。
- 长期不合并的分支每两天 `git rebase main` 一次，减少最后一天的冲突面。

其他前缀（同样遵循 Conventional Commits 语义）：
`fix/<字母>-<模块>`、`docs/<主题>`、`chore/<主题>`、`contract-change/<主题>`（见 §4）。

---

## 3. Commit Message

遵循 [Conventional Commits](https://www.conventionalcommits.org/)，**scope 用成员字母或模块名**：

```
feat(A): 实现列表页倒序翻页解析
fix(B): 修正无截止日文档被时间过滤误杀的问题
docs(contract): 补充 applied_filters 的两种形态说明
test(C): 为 chat handler 补充拒答判定用例
chore(infra): 统一 CRLF 为 LF
```

要求：

- `type` 只用 `feat` / `fix` / `docs` / `test` / `refactor` / `chore` / `perf` / `build`。
- 标题行 ≤ 72 字符，不加句号。
- **一个 commit 只做一件事**。"顺手改个格式"请单独一个 `chore` commit——
  混在功能 commit 里会让 `git bisect` 找不到真凶。
- 正文写**为什么**，不写"改了什么"（改了什么 `git diff` 会说）。
- 严禁把 `.env`、密钥、模型权重、`node_modules/`、`dist/` 提交上来。

---

## 4. 修改 API 契约或数据模型（特殊流程）

`docs/api.md` 与 chunk schema 是冻结的。修改它们不是普通 PR，必须走这条路径：

1. 先改 `docs/api.md`（人读的那份）。
2. **同一天内**同步 `.claude/skills/sgu-notice-rag-dev/references/api-contract.md`；
   若涉及字段，还要同步 `references/data-model.md` 与 `docs/data-model.md`。
   两份副本不同步，Agent 就会按旧契约生成代码，这是本项目最高频的事故来源。
3. 跑 `bash .claude/skills/sgu-notice-rag-dev/scripts/check-contract.sh`，必须 exit 0。
4. 交叉确认（谁改谁找对面签字）：
   - 改 HTTP 契约 → **C ↔ D** 双方确认
   - 改 chunk 字段 → **A ↔ B** 双方确认
   - 改评估口径 → **B ↔ E** 双方确认
5. PR 标题以 `contract-change:` 开头，让 Reviewer 一眼看到这是契约变更。

**唯一例外**：只改措辞、不改字段名与语义时，可以走普通 PR。

---

## 5. PR 流程

1. `git push -u origin feat/<字母>-<模块>`
2. 在 GitHub 发起 PR，按 [PR 模板](.github/PULL_REQUEST_TEMPLATE.md) 填写全部栏目。
3. 找交叉 Reviewer（见下表），**不能自己审自己**。
4. CI 三绿（lint / test / build）+ Reviewer 通过 → Merge（用 **Squash merge**，保持 `main` 线性）。
5. 合并后立刻删远端分支，避免下一个人 clone 下来看到一堆陈旧分支。

### 交叉 Reviewer 对照表

| 提 PR 的人 | 必须找谁审 | 为什么 |
|---|---|---|
| A | B | chunk 字段的唯一消费方是 B |
| B | A + E | 时效逻辑同时影响采集口径与评估口径 |
| C | D + F | D 是 HTTP 契约的消费方，F 管部署与故障面 |
| D | C | 请求层必须与 `docs/api.md` 逐字一致 |
| E | B | 指标口径与检索行为强耦合 |
| F | C | 服务名/端口/健康检查改错会打挂网关 |

---

## 6. 本地自测（提 PR 前必须跑）

```bash
# 契约自检 —— 任何 PR 都要跑
bash .claude/skills/sgu-notice-rag-dev/scripts/check-contract.sh

# 按你的模块跑（不是全跑，但你的模块必须绿）
cd ingestion && python -m pytest -q           # A：应 72 passed
cd rag-core  && python -m pytest -q           # B：应 43 passed
cd api-server && go vet ./... && go test ./... # C
cd frontend  && pnpm typecheck && pnpm test   # D：应 28 passed
cd eval      && python -m pytest -q           # E：应 56 passed
docker compose config --quiet                 # F
```

**测试没绿就提 PR 的，Reviewer 可以直接打回不必看代码。**
新增功能必须带测试；仅"跑通了"不算验收（见各 Issue 的验收命令）。

---

## 7. 遇到契约冲突怎么办

不要"先按自己的想法写，等冲突了再改"。顺序是：

1. 停下来，读 `SKILL.md` 的对应章节。
2. 如果是文档自身矛盾，提 issue 或直接在群里 @ 相关工作对。
3. 只有确认契约确需变更，才走 §4 流程。

相关文档：[README](README.md) · [PRD](docs/prd.md) · [API 契约](docs/api.md) ·
[数据模型](docs/data-model.md) · [启动指令](docs/kickoff.md) · [部署](docs/deployment.md)
