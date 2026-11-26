FROM python:3.12-slim AS builder
WORKDIR /build
COPY requirements.lock pyproject.toml ./
COPY backend ./backend
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir -r requirements.lock && /opt/venv/bin/pip install --no-cache-dir --no-deps .

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENV PATH="/opt/venv/bin:$PATH"
WORKDIR /app
RUN useradd --create-home --uid 1000 navigator && mkdir /data && chown navigator:navigator /data
COPY --from=builder /opt/venv /opt/venv
COPY alembic.ini LICENSE /app/
COPY backend /app/backend
COPY migrations /app/migrations
COPY frontend /app/frontend
USER navigator
ENV DATABASE_URL=sqlite:////data/research_navigator.db
LABEL org.opencontainers.image.source="https://github.com/Sharan099/Research-Navigator" \
      org.opencontainers.image.licenses="MIT"
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/ready', timeout=3)"
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

