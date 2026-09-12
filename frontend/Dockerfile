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
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
