# Build from the repo root: docker build -f ui/Dockerfile --build-arg DASHBOARD_API_URL=... .
FROM node:24-alpine AS build

RUN corepack enable
WORKDIR /app

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml turbo.json ./
COPY ui/package.json ui/package.json
RUN pnpm install --frozen-lockfile --filter open-swe-dashboard...

COPY ui ui

# Baked into the bundle as the `/dashboard/api/*` proxy target and the backend
# server renders call, so one image belongs to exactly one backend. No default:
# a fallback would be production's backend, silently inherited by every other
# deployment. See docs/PREVIEW_ENV.md.
ARG DASHBOARD_API_URL
RUN test -n "$DASHBOARD_API_URL" || (echo "DASHBOARD_API_URL build-arg is required" >&2; exit 1)
ENV DASHBOARD_API_URL=$DASHBOARD_API_URL
RUN pnpm --filter open-swe-dashboard run build

FROM node:24-alpine

WORKDIR /app
COPY --from=build /app/ui/.output ./.output

ENV NODE_ENV=production \
    HOST=0.0.0.0 \
    PORT=8080

USER node
EXPOSE 8080
CMD ["node", ".output/server/index.mjs"]
