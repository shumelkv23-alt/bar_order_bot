FROM node:22-alpine AS miniapp-build

WORKDIR /frontend
COPY miniapp/package.json miniapp/package-lock.json ./
RUN npm ci
COPY miniapp ./
RUN npm run build

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY app ./app
COPY --from=miniapp-build /frontend/dist ./app/web/miniapp
COPY alembic.ini ./
COPY alembic ./alembic

RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log"]
