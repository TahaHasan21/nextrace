# Nextrace frontend - Angular build served as static assets by nginx.
# The dev server (`ng serve`) is intentionally NOT used in this image.
#
# Two runtime targets share the same Angular build:
#   local (default, last stage) - nginx also proxies the API routes to
#       the backend; used by docker-compose.yml, unchanged.
#   aws - nginx serves only the SPA; the AWS ALB routes API paths to the
#       backend ECS service. Build with: docker build --target aws .

FROM node:22-slim AS build
WORKDIR /app

COPY package.json package-lock.json ./
# --legacy-peer-deps works around a known npm/arborist bug with Angular
# 22's new optional peer deps (vitest/msw/canvas) - see README.
RUN npm ci --legacy-peer-deps

COPY . .
RUN npm run build

# AWS/ECS production image: static SPA only, no API proxying - see
# nginx.aws.conf. Copied straight to conf.d (no templates/ directory), so
# the base image's envsubst step has nothing to render and no
# BACKEND_UPSTREAM is needed.
FROM nginx:1.27-alpine AS aws
COPY --from=build /app/dist/frontend/browser /usr/share/nginx/html
COPY nginx.aws.conf /etc/nginx/conf.d/default.conf
EXPOSE 80

# Local Docker Compose image (default target - keep this stage last so a
# plain `docker build` / `docker compose build` still produces it).
FROM nginx:1.27-alpine AS local
COPY --from=build /app/dist/frontend/browser /usr/share/nginx/html
# Rendered from a template, not copied directly - the base image's own
# entrypoint (docker-entrypoint.d/20-envsubst-on-templates.sh) substitutes
# ${BACKEND_UPSTREAM} at container startup and writes the result to
# /etc/nginx/conf.d/default.conf before nginx starts. See
# nginx.conf.template for why this is startup-time, not request-time,
# substitution.
COPY nginx.conf.template /etc/nginx/templates/default.conf.template

# Local Docker Compose default - matches the Compose service name/port
# exactly as before. Override at container run time to point at a
# different backend address; no rebuild needed. (On AWS, use the `aws`
# target instead - it doesn't proxy at all.)
ENV BACKEND_UPSTREAM=backend:8000

EXPOSE 80
