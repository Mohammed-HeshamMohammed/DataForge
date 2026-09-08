# Web service image. Browser automation runs against a separate Selenium
# container (see docker-compose.yml) rather than bundling a browser here.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY dataforge ./dataforge

RUN pip install --upgrade pip && \
    pip install ".[ml,web,scraping,scheduler,excel]"

# Run as a non-root user.
RUN useradd --create-home --uid 1000 dataforge && chown -R dataforge:dataforge /app
USER dataforge

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"

CMD ["uvicorn", "dataforge.web.app:app", "--host", "0.0.0.0", "--port", "8000"]
