FROM golang:1.24.8-bookworm AS build
# Official release source is pinned because MinIO registry images are no longer available.
RUN CGO_ENABLED=0 go install -ldflags="-s -w" github.com/minio/minio@RELEASE.2025-10-15T17-29-55Z

FROM alpine:3.22
RUN apk add --no-cache ca-certificates curl tzdata \
    && adduser -D -u 10001 minio \
    && mkdir /data \
    && chown minio:minio /data
COPY --from=build /go/bin/minio /usr/local/bin/minio
USER minio
EXPOSE 9000 9001
ENTRYPOINT ["minio"]
