# syntax=docker/dockerfile:1

FROM python:3.11-slim AS backend
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend backend
COPY assets assets
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
EXPOSE 8000
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM node:22-alpine AS frontend-build
WORKDIR /app
COPY frontend/package.json ./
RUN npm install
COPY frontend ./
ENV API_PROXY_URL=http://backend:8000
RUN npm run build

FROM node:22-alpine AS frontend
WORKDIR /app
ENV NODE_ENV=production
ENV API_PROXY_URL=http://backend:8000
COPY --from=frontend-build /app ./
EXPOSE 3000
CMD ["npm", "run", "start"]
