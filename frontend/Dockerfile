# syntax=docker/dockerfile:1.7
# ---- build the SPA --------------------------------------------------------------------
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
# NODE_ENV is unset here so devDependencies (vite, tsc) are installed for the build.
RUN npm ci
COPY . .
# Public (non-secret) build-time config, baked into the bundle.
ARG VITE_WS_URL=ws://localhost:8080/ws
ARG VITE_SHOW_DEMO_CREDENTIALS=true
ENV VITE_WS_URL=$VITE_WS_URL VITE_SHOW_DEMO_CREDENTIALS=$VITE_SHOW_DEMO_CREDENTIALS
RUN npm run build

# ---- serve it ---------------------------------------------------------------------------
FROM nginx:1.27-alpine AS runtime
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://127.0.0.1/healthz || exit 1
