#!/usr/bin/env bash
# 【F 所有】等待某个 TCP 端点可连接
# 用法: ./wait-for.sh host:port [timeout_seconds]
set -euo pipefail
hostport="${1:?usage: wait-for.sh host:port [timeout]}"
timeout="${2:-60}"
host="${hostport%%:*}"
port="${hostport##*:}"
for _ in $(seq 1 "$timeout"); do
  if (exec 3<>"/dev/tcp/$host/$port") 2>/dev/null; then
    echo "✓ $hostport ready"
    exit 0
  fi
  sleep 1
done
echo "✗ $hostport not ready after ${timeout}s" >&2
exit 1
