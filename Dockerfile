# 1) Build the Next.js front end as static files
FROM node:22-slim AS web
WORKDIR /web
COPY web-next/package.json web-next/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web-next ./
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# 2) The Python app, which also serves those files
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY brandopt ./brandopt
COPY fasla ./fasla
COPY web ./web
COPY --from=web /web/out ./web-next/out
COPY data/.keep ./data/.keep
RUN useradd -m fasla && chown -R fasla /app
USER fasla
EXPOSE 8000
# one worker process: plans run on background threads inside it
CMD ["sh", "-c", "uvicorn fasla.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
