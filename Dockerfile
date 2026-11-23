FROM node:24-bookworm-slim AS assets
WORKDIR /app
COPY package.json package-lock.json tsconfig.json ./
RUN --mount=type=secret,id=build_ca \
    if [ -f /run/secrets/build_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/build_ca; fi; \
    npm ci
COPY frontend/src frontend/src
COPY shared shared
RUN npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./
RUN --mount=type=secret,id=build_ca \
    if [ -f /run/secrets/build_ca ]; then export PIP_CERT=/run/secrets/build_ca; fi; \
    pip install --no-cache-dir -r requirements.txt
COPY backend backend
COPY frontend/templates frontend/templates
COPY frontend/static frontend/static
COPY shared shared
COPY --from=assets /app/frontend/dist frontend/dist
RUN DJANGO_SECRET_KEY="$(python -c 'from secrets import token_urlsafe; print(token_urlsafe(48))')" \
    python backend/manage.py collectstatic --noinput && \
    chmod -R a+rX backend frontend shared staticfiles
USER 65532:65532
CMD ["sh", "-c", "exec gunicorn --chdir /app/backend --bind 0.0.0.0:${PORT:-8080} --workers 2 lablink.wsgi:application"]
