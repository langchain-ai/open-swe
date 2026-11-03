# Build from the repo root: docker build -f ui/Dockerfile .
#
# The backend this dashboard fronts is read from DASHBOARD_API_URL at runtime, so
# one image serves any deployment. Set it in the deployment's environment.
FROM node:26-alpine AS build

RUN npm install --global pnpm@11.22.0
WORKDIR /app

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml turbo.json ./
COPY ui/package.json ui/package.json
COPY ui/patches ui/patches
COPY desktop/patches desktop/patches
RUN pnpm install --frozen-lockfile --filter open-swe-dashboard...

COPY ui ui

RUN pnpm --filter open-swe-dashboard run build

FROM node:26-alpine

WORKDIR /app
COPY --from=build /app/ui/.output ./.output

ENV NODE_ENV=production \
    HOST=0.0.0.0 \
    PORT=8080

USER node
EXPOSE 8080
CMD ["node", ".output/server/index.mjs"]
