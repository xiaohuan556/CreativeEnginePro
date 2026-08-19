FROM node:22-bookworm-slim AS dependencies
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM dependencies AS builder
ARG NEXT_PUBLIC_CONTROL_PLANE_URL=/control
ENV NEXT_PUBLIC_CONTROL_PLANE_URL=${NEXT_PUBLIC_CONTROL_PLANE_URL}
COPY . .
RUN npm run build

FROM node:22-bookworm-slim AS runtime
ENV NODE_ENV=production \
    PORT=3000
WORKDIR /app
COPY --from=dependencies /app/node_modules ./node_modules
COPY --from=builder /app/dist ./dist
COPY --from=builder /app/package.json ./package.json
USER node
EXPOSE 3000
HEALTHCHECK --interval=20s --timeout=5s --start-period=15s --retries=5 \
  CMD node -e "fetch('http://127.0.0.1:3000/').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"
CMD ["./node_modules/.bin/vinext", "start", "--hostname", "0.0.0.0", "--port", "3000"]
