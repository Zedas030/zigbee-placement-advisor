FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN useradd --system --uid 10001 --home-dir /app zpa
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY zpa ./zpa

# `docker build --target test .` runs the full test suite inside the image (same arch as production)
FROM base AS test
COPY requirements-dev.txt pyproject.toml config.example.yaml ./
COPY tests ./tests
RUN pip install -r requirements-dev.txt && python -m pytest -q -p no:cacheprovider

FROM base AS runtime
USER zpa
VOLUME ["/data"]
EXPOSE 8099
HEALTHCHECK --interval=60s --timeout=10s --start-period=90s --retries=3 \
  CMD ["python", "-m", "zpa", "healthcheck", "--config", "/config/config.yaml"]
ENTRYPOINT ["python", "-m", "zpa"]
CMD ["run", "--config", "/config/config.yaml"]
