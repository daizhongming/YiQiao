# This file was modified in 2026 by YiQiao contributors. See NOTICE.

FROM node:20-alpine AS base
ARG NPM_REGISTRY=https://registry.npmjs.org
RUN apk add --no-cache libc6-compat \
    && npm install --global pnpm@10.34.2 \
        --registry="${NPM_REGISTRY}" \
        --fetch-retries=5 \
        --fetch-retry-mintimeout=2000 \
        --fetch-retry-maxtimeout=30000
WORKDIR /app

# Dependencies
FROM base AS deps
COPY server/dashboard/package.json server/dashboard/pnpm-lock.yaml server/dashboard/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
RUN set -eu; \
    mkdir -p /licenses/npm/packages; \
    find node_modules/.pnpm -type f \
      \( -iname 'license*' -o -iname 'licence*' -o -iname 'notice*' -o -iname 'copying*' -o -name package.json \) \
      -print | while IFS= read -r source; do \
        target="/licenses/npm/packages/${source#node_modules/.pnpm/}"; \
        mkdir -p "$(dirname "$target")"; \
        cp "$source" "$target"; \
      done; \
    pnpm licenses list --prod --json > /licenses/npm/production-licenses.json; \
    test -s /licenses/npm/production-licenses.json; \
    test "$(find /licenses/npm/packages -type f | wc -l)" -ge 100

# Build
FROM base AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY server/dashboard .

ENV NEXT_TELEMETRY_DISABLED=1
# Placeholder values replaced at runtime by entrypoint.sh
ENV NEXT_PUBLIC_API_URL=NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_INSTANCE_NAME=NEXT_PUBLIC_INSTANCE_NAME

RUN pnpm run build

# Production
FROM base AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

RUN addgroup --system --gid 1001 nodejs && \
    adduser --system --uid 1001 nextjs

COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY LICENSE NOTICE THIRD_PARTY_NOTICES.md MODIFICATIONS.md /usr/share/licenses/yiqiao/
COPY server/dashboard/public/fonts/FONT_LICENSES.md server/dashboard/public/fonts/OFL-1.1.txt /usr/share/licenses/yiqiao/fonts/
COPY LICENSE /usr/share/licenses/yiqiao/fonts/RobotoMono-Apache-2.0.txt
COPY --from=deps /licenses/npm /usr/share/licenses/yiqiao/npm
COPY --chown=nextjs:nodejs server/dashboard/entrypoint.sh /home/nextjs/entrypoint.sh
RUN sed -i 's/\r$//' /home/nextjs/entrypoint.sh \
    && chmod +x /home/nextjs/entrypoint.sh \
    && test -s /usr/share/licenses/yiqiao/LICENSE \
    && test -s /usr/share/licenses/yiqiao/NOTICE \
    && test -s /usr/share/licenses/yiqiao/THIRD_PARTY_NOTICES.md \
    && test -s /usr/share/licenses/yiqiao/MODIFICATIONS.md \
    && test -s /usr/share/licenses/yiqiao/fonts/FONT_LICENSES.md \
    && test -s /usr/share/licenses/yiqiao/fonts/OFL-1.1.txt \
    && test -s /usr/share/licenses/yiqiao/fonts/RobotoMono-Apache-2.0.txt \
    && test -s /usr/share/licenses/yiqiao/npm/production-licenses.json \
    && test "$(find /usr/share/licenses/yiqiao/npm/packages -type f | wc -l)" -ge 100

USER nextjs

EXPOSE 3000
ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=6 \
  CMD wget -qO- http://127.0.0.1:3000/api/health >/dev/null || exit 1

ENTRYPOINT ["/home/nextjs/entrypoint.sh"]
CMD ["node", "server.js"]
