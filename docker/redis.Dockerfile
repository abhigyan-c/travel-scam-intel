# Preserve Redis 7.4 and its Alpine family/AOF format; patch bundled OS libraries.
FROM redis:7.4-alpine@sha256:7f7d30b7fdd09467bac58198a1f166dd69789d348d4fe859cccb20e0cf287fbe
RUN apk upgrade --no-cache && redis-server --version
