FROM golang:1.27.4-trixie AS gosu
ADD --checksum=sha256:207a2045e890d3ca3326027c5f585733085960b401a49dbc00e3bb628c054cf8 https://codeload.github.com/tianon/gosu/tar.gz/refs/tags/1.20 /tmp/gosu.tar.gz
WORKDIR /src
RUN tar -xzf /tmp/gosu.tar.gz --strip-components=1 \
    && go get golang.org/x/sys@v0.44.0 \
    && CGO_ENABLED=0 go build -trimpath -o /gosu .

# Preserve the database major, extension version, UID and libc/locale family.
FROM pgvector/pgvector:0.8.6-pg17-trixie@sha256:724a4041afdb1750446e3f6b5cfa8f3b0ac5a2cf538ddfa6bfee4f94c2fa85c6
# Debian Trixie supplies PostgreSQL 17 security releases. Use its signed indexes
# consistently rather than silently proceeding after a failed PGDG mirror fetch.
RUN rm /etc/apt/sources.list.d/pgdg.list \
    && apt-get -o APT::Update::Error-Mode=any -o Acquire::Retries=3 update \
    && apt-get upgrade --yes \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' postgresql-17)" ge 17.12 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=gosu /gosu /usr/local/bin/gosu
RUN gosu postgres id && postgres --version
