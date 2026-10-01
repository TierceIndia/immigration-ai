FROM python:3.12-slim AS base


ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app


# gcc/libpq-dev are needed to build the psycopg2 source distribution.
# No Xvfb/Xauth: the browser runs headless, so no X display is ever required.
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*


COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


# Patchright drives a real Chromium session. It is headless by default, so the
# browser works with no X server and no DISPLAY; --with-deps pulls the system
# libraries Chromium needs. The Playwright package is deliberately not installed.
RUN patchright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*


COPY src/ src/
COPY sources/ sources/
COPY schema/ schema/
COPY migrations/ migrations/
COPY prompts/ prompts/
COPY alembic.ini .

EXPOSE 8000

CMD ["uvicorn", "src.api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]