#!/usr/bin/env python3
"""用一个假的 Qdrant HTTP 端点验证 init-qdrant.sh 的行为。

这不是"跑个测试图个安心"——脚本有三个容易错的点，都只有对着真实 HTTP 才看得出来：

  1. **幂等性**：Qdrant 的 `PUT /collections/{name}` 在集合已存在时会**重建**，
     所以脚本必须先 GET 再决定 PUT。跑两次，第二次不能出现 PUT。
  2. **失败要非 0 退出**：rag-core 的 `depends_on: service_completed_successfully`
     挂在它身上，脚本静默成功 = 让检索服务对着空库跑。
  3. **索引必须齐**：六个 payload 索引一个都不能少，少了时间过滤会退化成全表扫。

用法：python infra/scripts/test-init-qdrant.py
"""

from __future__ import annotations

import http.server
import json
import os
import subprocess
import sys
import threading
import urllib.parse
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "init-qdrant.sh"
COLL = "sgu_notices"

EXPECTED_INDEXES = {
    "publish_date": "datetime",
    "effective_from": "datetime",
    "effective_to": "datetime",
    "is_current": "bool",
    "doc_version_rank": "integer",
    "content_type": "keyword",
}


class FakeQdrant:
    """最小可用的 Qdrant 假端点，只实现脚本会打到的那几个路由。"""

    def __init__(self) -> None:
        self.collections: set[str] = set()
        self.indexes: dict[str, str] = {}
        self.requests: list[tuple[str, str]] = []
        self.lock = threading.Lock()

    def reset_log(self) -> None:
        with self.lock:
            self.requests = []

    def writes(self) -> list[tuple[str, str]]:
        """只看有副作用的请求。"""
        with self.lock:
            return [r for r in self.requests if r[0] in ("PUT", "POST", "DELETE")]

    def handler(self) -> type[http.server.BaseHTTPRequestHandler]:
        store = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):  # 安静
                pass

            def _send(self, code: int, payload: dict | None = None) -> None:
                body = json.dumps(payload or {}).encode()
                try:
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    # curl -sf -o /dev/null 拿到响应就关连接，这里是正常现象。
                    # 不吞掉的话每次探测都会在 stderr 打一段 WinError 10053 噪音，
                    # 而噪音会让人习惯性忽略测试输出 —— 那比没有测试更糟。
                    self.close_connection = True

            def _path(self) -> tuple[str, list[str]]:
                u = urllib.parse.urlparse(self.path)
                parts = [p for p in u.path.split("/") if p]
                return u.path, parts

            def _collect_name(self, parts: list[str]) -> str | None:
                # /collections/{name}[/index]
                if len(parts) >= 2 and parts[0] == "collections":
                    return parts[1]
                return None

            def do_GET(self):  # noqa: N802
                path, parts = self._path()
                with store.lock:
                    store.requests.append(("GET", path))
                if path == "/collections":
                    return self._send(200, {"result": {"collections": []}})
                name = self._collect_name(parts)
                if name:
                    with store.lock:
                        exists = name in store.collections
                    if exists:
                        return self._send(200, {"result": {"name": name}})
                    return self._send(404, {"status": {"error": "not found"}})
                return self._send(404)

            def do_PUT(self):  # noqa: N802
                path, parts = self._path()
                with store.lock:
                    store.requests.append(("PUT", path))
                name = self._collect_name(parts)
                if not name:
                    return self._send(400)
                if len(parts) == 2:
                    # 模拟 Qdrant 语义：已存在则重建（这正是脚本必须避免的）
                    with store.lock:
                        store.collections.add(name)
                        store.indexes.clear()
                    return self._send(200, {"result": True})
                if len(parts) == 3 and parts[2] == "index":
                    n = int(self.headers.get("Content-Length", "0"))
                    body = json.loads(self.rfile.read(n) or b"{}")
                    with store.lock:
                        store.indexes[body.get("field_name", "")] = body.get(
                            "field_schema", ""
                        )
                    return self._send(200, {"result": True})
                return self._send(404)

            def do_DELETE(self):  # noqa: N802
                path, parts = self._path()
                with store.lock:
                    store.requests.append(("DELETE", path))
                name = self._collect_name(parts)
                if name:
                    with store.lock:
                        existed = name in store.collections
                        store.collections.discard(name)
                    return self._send(200 if existed else 404)
                return self._send(404)

        return H


def run_script(url: str, wait: str = "15") -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["VECTOR_DB_URL"] = url
    env["QDRANT_COLLECTION"] = COLL
    env["QDRANT_WAIT_SECONDS"] = wait
    return subprocess.run(
        ["sh", str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def main() -> int:
    store = FakeQdrant()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), store.handler())
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}"
    threading.Thread(target=server.serve_forever, daemon=True).start()

    failures: list[str] = []
    try:
        # ---------- 第 1 次：空库 ----------
        store.reset_log()
        r1 = run_script(url)
        print("=== 第一次（空库）===")
        print(r1.stdout.rstrip())
        if r1.returncode != 0:
            failures.append(f"首次运行退出码 {r1.returncode}\n{r1.stderr}")
        if COLL not in store.collections:
            failures.append("首次运行后 collection 不存在")
        if store.indexes != EXPECTED_INDEXES:
            missing = set(EXPECTED_INDEXES) - set(store.indexes)
            wrong = {
                k: (v, store.indexes.get(k))
                for k, v in EXPECTED_INDEXES.items()
                if k in store.indexes and store.indexes[k] != v
            }
            failures.append(f"索引不齐：缺 {sorted(missing)}，类型不符 {wrong}")
        puts1 = [p for m, p in store.writes() if m == "PUT" and p.count("/") == 2]
        if len(puts1) != 1:
            failures.append(f"首次应对 collection 发 1 次 PUT，实际 {len(puts1)}")

        # ---------- 第 2 次：幂等 ----------
        store.reset_log()
        r2 = run_script(url)
        print("\n=== 第二次（已存在，应不重建）===")
        print(r2.stdout.rstrip())
        if r2.returncode != 0:
            failures.append(f"第二次运行退出码 {r2.returncode}\n{r2.stderr}")
        rebuilds = [p for m, p in store.writes() if m == "PUT" and p.count("/") == 2]
        if rebuilds:
            failures.append(
                f"第二次运行重建了 collection（{rebuilds}）——"
                "这会把向量库清空，collection 已存在时必须跳过 PUT"
            )
        if COLL not in store.collections:
            failures.append("第二次运行后 collection 消失了（被重建破坏了）")
        if store.indexes != EXPECTED_INDEXES:
            failures.append("第二次运行后索引丢失")

        # ---------- 第 3 次：Qdrant 不可达，必须非 0 ----------
        print("\n=== 第三次（Qdrant 不可达，应失败）===")
        r3 = run_script("http://127.0.0.1:1", wait="2")
        print(r3.stdout.rstrip() or "(stdout 空)")
        if r3.stderr.strip():
            print(r3.stderr.rstrip())
        if r3.returncode == 0:
            failures.append(
                "Qdrant 不可达时脚本返回 0 —— rag-core 会因此启动并对着空库跑"
            )
        else:
            print(f"✓ 退出码 {r3.returncode}")
        if "没有就绪" not in r3.stderr:
            failures.append("超时时的报错信息没有指出是哪一步失败的（缺 '没有就绪'）")

    finally:
        server.shutdown()

    print()
    if failures:
        print("✗ 失败：")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("✓ init-qdrant.sh 三项行为全部符合预期（幂等 / 索引齐全 / 失败非 0）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
