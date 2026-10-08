FROM python:3.12.8-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN addgroup --system tracegrade && adduser --system --ingroup tracegrade tracegrade

COPY pyproject.toml README.md ./
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic
RUN pip install --no-cache-dir .

USER tracegrade
EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
