FROM node:22-alpine AS build
WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ .
RUN npx ng build --configuration production

# Unprivileged nginx (uid 101, port 8080). ${API_UPSTREAM} is substituted at start-up from the environment.
FROM nginxinc/nginx-unprivileged:1.27-alpine
ENV API_UPSTREAM=http://backend:8000
COPY frontend/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist/frontend/browser /usr/share/nginx/html
EXPOSE 8080
