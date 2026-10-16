FROM node:22.18.0-bookworm-slim@sha256:752ea8a2f758c34002a0461bd9f1cee4f9a3c36d48494586f60ffce1fc708e0e AS runtime
WORKDIR /app
COPY search-service/package*.json ./
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund
COPY search-service/ ./
ENV NODE_ENV=production LLM_GENERATION_ENABLED=false
USER 1001:1001
CMD ["node", "index.js"]
