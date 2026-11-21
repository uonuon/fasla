FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY brandopt ./brandopt
COPY fasla ./fasla
COPY web ./web
COPY data/.keep ./data/.keep
RUN useradd -m fasla && chown -R fasla /app
USER fasla
EXPOSE 8000
# one worker process: plans run on background threads inside it
CMD ["sh", "-c", "uvicorn fasla.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
