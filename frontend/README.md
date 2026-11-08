# frontend（D 组 · Vue 3 + TS + Vite）

教学通知问答的前端。三个页面：**问答**（流式）、**通知清单**、**评估看板**。

## 跑起来

```bash
pnpm install          # 或 npm install
pnpm dev              # http://localhost:5173
```

**默认走 Mock，后端不用起。** `VITE_USE_MOCK=true` 是默认值（`src/api/mode.ts`
里只有显式写 `false` 才关），所以新人 clone 下来直接 `pnpm dev` 就能点通全链路。

接真后端：

```bash
# .env.development.local
VITE_USE_MOCK=false
VITE_API_BASE_URL=/api        # vite.config.ts 里已配 /api → localhost:8080 代理
```

## 目录

```
src/
├─ api/                  唯一的数据入口，组件不直接 fetch
│  ├─ client.ts          baseURL / 拆包 / 错误映射 / JWT / 超时
│  ├─ types.ts           与 docs/api.md 逐字对应的类型
│  ├─ auth.ts  notices.ts  chat.ts  eval.ts
│  ├─ mode.ts            Mock 开关的唯一读取点
│  ├─ mock/fixtures.ts   假数据（含两条陷阱样例，见下）
│  └─ __tests__/         contract.spec.ts + client.spec.ts
├─ components/
│  ├─ TimeBadge.vue      ★ 时效角标 —— 整个产品的核心 UI
│  └─ CitationCard.vue   引用卡片
├─ views/                ChatView / NoticesView / EvalView
├─ router/               /chat /notices /eval
└─ main.ts  App.vue
```

## 三条硬规矩

1. **不许硬编码后端地址。** 只认 `VITE_API_BASE_URL`。`check-contract.sh` 会扫
   `localhost:PORT/api` 这类字面量并报错。
2. **字段名不许自造。** 改任何字段前先改 `docs/api.md`（走 SKILL.md §1 三步流程），
   再同步 `types.ts` + `mock/fixtures.ts`，最后 `pnpm test`。`contract.spec.ts`
   会对每个响应体做键集合比对，少一个多一个都红。
3. **`effective_to === null` 是"长期有效"，不是"已过期"。** 渲染成"已过期"是
   本项目最典型的回归 —— Mock 里专门留了一条 `韶关学院学生学籍管理规定` 守这个。

## 测试

```bash
pnpm test        # 28 passed
pnpm typecheck   # vue-tsc --noEmit
pnpm build       # 产物在 dist/
```

`contract.spec.ts`（20 例）是契约漂移探测：键集合、枚举取值、日期格式、以及
"拒答响应的形状必须与非拒答响应一致"（前端不能靠字段在不在判断拒答）。

`client.spec.ts`（8 例）只测错误映射，因为那是唯一"改坏了平时看不出来"的逻辑：
网关返回 HTML 错误页、429 带 `Retry-After`、超时映射成 `50202`。

## Mock 数据里的两条陷阱

| 样例 | 为什么放它 |
|---|---|
| `韶关学院学生学籍管理规定`，`effective_to: null` | 端不到这条，就会把"长期有效"渲染成"已过期" |
| `关于做好2025年转专业工作的通知`，`is_current: false` | 端不到这条，就会把旧版本当现行规定展示 |

另外 Mock 的 `/不存在|没有|未知/` 会触发拒答分支，方便调试空态与拒答话术。

## SSE 注意事项

- 契约是 **GET + `?q=&token=`**，因为 `EventSource` 不能设请求头。token 走 query
  是唯一可行的折衷，所以后端日志里要脱敏（C 组已处理）。
- 事件顺序固定 `meta → sources → delta×N → done`。`done` 之后组件必须停止追加。
- 浏览器断线时 `onerror` 没有 `data`，和"服务端显式发 error 事件"要分开处理
  —— `chat.ts` 里已区分，别改成只看 `ev.data`。
- Mock 的流按句切分、`setTimeout` 模拟节奏，`close()` 会清掉所有定时器。改 Mock
  时注意别忘了 `closed` 标志，否则 `close()` 之后还会吐字。

## 构建产物

`Dockerfile` 是两阶段（node build → nginx:alpine）。**Vite 的环境变量在 build 期
被内联**，所以 `docker-compose.yml` 里用的是 `build.args` 而不是 `environment`。
改 `VITE_USE_MOCK` 需要重新 build，不是重启容器。

## 分工边界

- 本目录归 **D**。改 API 契约要拉 **C** 一起确认。
- `src/api/*` 之外的组件不碰数据层；数据层的改动**必须**同 PR 带上
  `mock/fixtures.ts` 与 `__tests__`。
- 评估看板的数字由 **E** 写入 Postgres，D 只负责画。
