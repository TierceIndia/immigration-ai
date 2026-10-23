# Build the admin UI, then serve it from nginx with /api proxied to the API.
#
# The bundle is built with VITE_API_BASE_URL="" so every request goes to the
# same origin the page was served from and nginx forwards /api to the API
# container. That keeps the browser off a second origin entirely: no CORS
# preflight, no hard-coded localhost:8000 baked into the bundle, and the JWT
# never travels cross-origin.
FROM node:22-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
# npm ci (not install): build the exact locked tree, so an image rebuild can
# never silently pick up a different dependency version than the one tested.
RUN npm ci

COPY index.html vite.config.js ./
COPY src/ src/

ENV VITE_API_BASE_URL=""
RUN npm run build


FROM nginx:1.27-alpine AS runtime

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

# nginx's own image already runs it in the foreground with the right signals.
