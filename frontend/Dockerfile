# 构建产物是纯静态文件，由 Nginx 承接；/api 由同一台 Nginx 反代到 Go。
#
# Vite 的环境变量在 **build 时** 被内联进产物，运行期再设没用。
# 所以这里用 ARG 而不是 ENV —— 改 mock 开关必须重新 build。
FROM node:22-alpine AS build
WORKDIR /app

ARG VITE_USE_MOCK=true
ARG VITE_API_BASE_URL=/api
ENV VITE_USE_MOCK=$VITE_USE_MOCK \
    VITE_API_BASE_URL=$VITE_API_BASE_URL

# 先只拷依赖清单，命中缓存
COPY package.json pnpm-lock.yaml* ./
RUN corepack enable && (pnpm install --frozen-lockfile || pnpm install)

COPY . .
RUN pnpm build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
