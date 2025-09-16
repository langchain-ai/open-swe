FROM node:20

WORKDIR /app

RUN corepack enable \
  && corepack prepare yarn@3.5.1 --activate

COPY package.json yarn.lock turbo.json tsconfig.json langgraph.json .yarnrc.yml ./
COPY .yarn/ .yarn/
COPY apps ./apps
COPY packages ./packages
COPY static ./static

RUN yarn install
RUN yarn build

CMD ["yarn", "workspace", "@openswe/agent", "dev"]
