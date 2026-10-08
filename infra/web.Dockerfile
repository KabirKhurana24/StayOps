# syntax=docker/dockerfile:1.7
# Build context: repository root (the web app depends on packages/shared).
FROM node:24-slim AS deps
RUN corepack enable
WORKDIR /repo
COPY pnpm-workspace.yaml package.json pnpm-lock.yaml ./
COPY apps/web/package.json apps/web/
COPY packages/shared/package.json packages/shared/
RUN pnpm install --frozen-lockfile --filter @stayops/web...

FROM deps AS build
COPY packages/shared packages/shared
COPY apps/web apps/web
# NEXT_PUBLIC_* values are inlined at build time.
ARG NEXT_PUBLIC_SUPABASE_URL
ARG NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY
ARG NEXT_PUBLIC_API_URL
RUN pnpm --filter @stayops/web build

FROM node:24-slim AS run
RUN corepack enable && useradd --create-home --uid 10001 stayops
WORKDIR /repo
ENV NODE_ENV=production
COPY --from=build --chown=stayops /repo /repo
USER stayops
EXPOSE 3000
CMD ["pnpm", "--filter", "@stayops/web", "start"]
