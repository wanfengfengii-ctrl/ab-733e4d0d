FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# Install production and test dependencies. The image doubles as the
# one-shot `verify` runner, so pytest/httpx are present at build time and
# nothing needs to be installed (or run as root) at container start.
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

# Application and test/smoke sources.
COPY app ./app
COPY tests ./tests
COPY scripts ./scripts

# Run as an unprivileged user.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /srv
USER appuser

EXPOSE 8000

# Health check confirms the process is actually accepting HTTP requests.
# Uses the stdlib (the slim image ships no curl); /health returns 200 only
# once the ASGI app is mounted and routing requests.
HEALTHCHECK --interval=5s --timeout=3s --start-period=10s --retries=12 \
    CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
