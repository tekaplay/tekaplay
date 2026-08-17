FROM node:20-alpine AS deps
WORKDIR /app
# npm ci (not install) so the build is reproducible from package-lock.json.
COPY package.json package-lock.json ./
RUN npm ci

FROM node:20-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# NEXT_PUBLIC_* values are inlined into the client bundle at BUILD time, so
# the API URL has to arrive as a build argument. Supplying it at runtime has
# no effect — the string is already compiled into the JavaScript. The default
# only exists so a bare `docker build` still produces a working dev image.
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL
RUN npm run build

FROM node:20-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production
# Standalone's server.js binds process.env.HOSTNAME; without this it can
# listen on localhost only and the platform's health check never passes.
ENV HOSTNAME=0.0.0.0
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
USER node
EXPOSE 3000
CMD ["node", "server.js"]
