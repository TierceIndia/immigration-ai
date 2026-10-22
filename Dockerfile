FROM python:3.12-slim AS base


ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # Browsers go to a fixed, readable location rather than root's HOME, so the
    # path is the same whichever user the container ends up running as.
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright

WORKDIR /app


# gcc/libpq-dev are needed to build the psycopg2 source distribution.
# No Xvfb/Xauth: the browser runs headless, so no X display is ever required.
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*


COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


# Playwright drives a real Chromium session - the one and only retrieval path
# (src/fetch/playwright_client.py). Baking the browser into the image means a
# container never downloads one at runtime: a fetch either works on a cold
# start or the image is wrong, instead of failing the first sync of every new
# container. --with-deps pulls the system libraries Chromium needs; headless
# means no X server and no DISPLAY.
RUN playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

# Patchright is still installed by requirements.txt for the retired fetch path
# behind FETCH_BACKEND=legacy, which exists only until the live check against a
# real source confirms the port. Its browser is deliberately NOT installed: the
# legacy path is not meant to run in a container.


COPY src/ src/
COPY sources/ sources/
COPY schema/ schema/
COPY migrations/ migrations/
COPY prompts/ prompts/
COPY alembic.ini .

EXPOSE 8000

# A liveness probe inside the image, so `docker compose ps` and any orchestrator
# report the app's own view of itself rather than just "the process exists".
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "src.api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
