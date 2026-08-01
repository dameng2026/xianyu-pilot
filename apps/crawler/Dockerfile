# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e
FROM node:25-bookworm-slim@sha256:81db02c4b671288a03915da9534dbd54f96d0e7c24d80ccc54f5b36b2e684370 AS build

WORKDIR /app
COPY package.json package-lock.json .npmrc ./
RUN npm ci --ignore-scripts --no-audit --no-fund

COPY tsconfig.json ./
COPY src ./src
RUN npm run build \
    && npm prune --omit=dev --ignore-scripts \
    && npm cache clean --force

# Keep the Playwright runtime and browser version exactly aligned with the
# package lock. The final process runs as the image's unprivileged pwuser.
FROM mcr.microsoft.com/playwright:v1.61.1-noble@sha256:5b8f294aff9041b7191c34a4bab3ac270157a28774d4b0660e9743297b697e48

ENV NODE_ENV=production \
    HOME=/home/pwuser \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
ENV HOST=0.0.0.0
ENV PORT=3001
WORKDIR /app

COPY --from=build --chown=pwuser:pwuser /app/package.json /app/package-lock.json ./
COPY --from=build --chown=pwuser:pwuser /app/node_modules ./node_modules
COPY --from=build --chown=pwuser:pwuser /app/dist ./dist

USER pwuser
EXPOSE 3001
CMD ["node", "dist/server.js"]
