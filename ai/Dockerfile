FROM node:22-slim AS build
WORKDIR /ai
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY tsconfig.json ./
COPY src ./src
RUN npm run build && npm prune --omit=dev

FROM node:22-slim
ENV NODE_ENV=production
WORKDIR /ai
COPY --from=build /ai/package.json ./
COPY --from=build /ai/node_modules ./node_modules
COPY --from=build /ai/dist ./dist
USER node
EXPOSE 3001
CMD ["node", "dist/main.js"]
