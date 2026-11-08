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

# 先只拷依赖清单，命中缓存。
#
# `pnpm-workspace.yaml` 必须一起拷：esbuild 有 postinstall（下载平台二进制），
# pnpm 10+ 默认不跑构建脚本，放行配置只在这个文件里。少拷一个文件，
# `pnpm install` 就会报 ERR_PNPM_IGNORED_BUILDS 直接失败 ——
# 而且本地（有那个文件）永远复现不出来。
COPY package.json pnpm-lock.yaml* pnpm-workspace.yaml ./
# 固定 pnpm 版本：corepack 默认会去下"当前最新"，那等于每次构建都可能换工具链，
# 一旦某一代 pnpm 改了 lockfile 语义，构建就在你没有改任何代码的日子里挂掉。
# 不在 package.json 写 packageManager 字段，是因为那会与 CI 里
# pnpm/action-setup@v4 显式指定的 version 冲突（它要求二者只出现一个）。
RUN corepack prepare pnpm@12.18.0 --activate \
    && pnpm install --frozen-lockfile

COPY . .
RUN pnpm build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
