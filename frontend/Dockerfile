# Nextrace frontend - Angular build served as static assets by nginx.
# The dev server (`ng serve`) is intentionally NOT used in this image.

FROM node:22-slim AS build
WORKDIR /app

COPY package.json package-lock.json ./
# --legacy-peer-deps works around a known npm/arborist bug with Angular
# 22's new optional peer deps (vitest/msw/canvas) - see README.
RUN npm ci --legacy-peer-deps

COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist/frontend/browser /usr/share/nginx/html
# Rendered from a template, not copied directly - the base image's own
# entrypoint (docker-entrypoint.d/20-envsubst-on-templates.sh) substitutes
# ${BACKEND_UPSTREAM} at container startup and writes the result to
# /etc/nginx/conf.d/default.conf before nginx starts. See
# nginx.conf.template for why this is startup-time, not request-time,
# substitution.
COPY nginx.conf.template /etc/nginx/templates/default.conf.template

# Local Docker Compose default - matches the Compose service name/port
# exactly as before. Override at container/task-definition run time
# (e.g. on ECS) to point at a different backend address; no rebuild
# needed.
ENV BACKEND_UPSTREAM=backend:8000

EXPOSE 80
