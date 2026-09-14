FROM node:22-alpine AS web-build
WORKDIR /src/dashboard
COPY dashboard/package*.json ./
RUN npm ci
# The public URLs are declared after `npm ci`, not before it. A build argument
# is part of the cache key of every later instruction, so declaring them first
# made the CI smoke build (no URLs) and the production build (real URLs)
# reinstall node_modules separately. `npm ci` never reads them; only the Vite
# build below bakes them into the bundle.
ARG VITE_OPERATIONS_PUBLIC_URL=
ARG VITE_MARKETING_PUBLIC_URL=
ENV VITE_OPERATIONS_PUBLIC_URL=${VITE_OPERATIONS_PUBLIC_URL}
ENV VITE_MARKETING_PUBLIC_URL=${VITE_MARKETING_PUBLIC_URL}
COPY dashboard/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
COPY --from=web-build /src/static ./static

# Identify the running deployment. Baked in at build time so /version can
# report exactly which commit is serving, which rollback depends on.
ARG RELEASE_SHA=unknown
ARG RELEASE_BUILT_AT=unknown
ENV RELEASE_SHA=$RELEASE_SHA
ENV RELEASE_BUILT_AT=$RELEASE_BUILT_AT
# The same commit as an image label, so a deploy can check what it pulled
# before it starts it, not only what /version says after.
LABEL org.opencontainers.image.revision=$RELEASE_SHA       org.opencontainers.image.created=$RELEASE_BUILT_AT       org.opencontainers.image.title=teleautomation-operations
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
