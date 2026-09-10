FROM python:3.12-slim AS base


ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app


RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
        xvfb \
    && rm -rf /var/lib/apt/lists/*


COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


RUN pip install --no-cache-dir playwright \
    && playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*


COPY src/ src/
COPY sources/ sources/
COPY schema/ schema/
COPY migrations/ migrations/
COPY alembic.ini .

EXPOSE 8000

CMD ["xvfb-run", "--auto-servernum", "--server-args=-screen 0 1920x1080x24", "uvicorn", "src.api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
