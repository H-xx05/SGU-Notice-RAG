# syntax=docker/dockerfile:1
# 【C 所有】api-server 镜像。
# 多阶段：builder 编译静态二进制 → runtime 只留二进制 + ca-certificates。
# 产物 < 30MB，且不含 Go 工具链（安全 + 拉取快）。

# ---------- builder ----------
FROM golang:1.23-alpine AS builder

WORKDIR /src

# 先只拷依赖清单，让 go mod download 层可缓存（改代码不重下依赖）
COPY go.mod go.sum* ./
RUN go mod download || true

COPY . .

ENV CGO_ENABLED=0 GOOS=linux
RUN go build -trimpath -ldflags="-s -w" -o /out/api-server ./cmd/server

# ---------- runtime ----------
FROM alpine:3.20

# wget 供 compose healthcheck 使用；tzdata 保证 Asia/Shanghai 可用
RUN apk add --no-cache ca-certificates tzdata wget \
    && adduser -D -u 10001 app

WORKDIR /app
COPY --from=builder /out/api-server /app/api-server

ENV TZ=Asia/Shanghai \
    API_PORT=8080 \
    APP_ENV=production

USER 10001
EXPOSE 8080

# 直接 exec 二进制，保证 PID 1 能收到 SIGTERM（优雅退出）
ENTRYPOINT ["/app/api-server"]
