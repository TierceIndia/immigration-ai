FROM python:3.12-slim AS base


ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app


RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
        xvfb \
        xauth \
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
COPY prompts/ prompts/
COPY alembic.ini .

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000

# browser_fetcher.py launches Chromium with headless=False (headless mode is
# fingerprinted and blocked by the Home Affairs WAF) — it needs a real X display.
# docker-entrypoint.sh starts Xvfb directly and waits for its socket rather than
# using xvfb-run's wrapper, whose SIGUSR1-based readiness handshake hangs under
# Docker's default (no-init) PID 1 signal handling.
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["uvicorn", "src.api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
