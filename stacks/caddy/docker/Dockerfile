# syntax=docker/dockerfile:1.7

ARG CADDY_VERSION=2.11.7
ARG CADDY_DNS_CLOUDFLARE_REF=v0.2.4

FROM --platform=$BUILDPLATFORM caddy:${CADDY_VERSION}-builder AS builder

ARG CADDY_VERSION
ARG CADDY_DNS_CLOUDFLARE_REF
ARG TARGETOS
ARG TARGETARCH
ARG TARGETVARIANT

# TODO: remove the x/net replace after a Caddy release includes v0.60.0 or newer.
RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    set -eu; \
    module="github.com/caddy-dns/cloudflare@${CADDY_DNS_CLOUDFLARE_REF}"; \
    net_replace="golang.org/x/net@v0.59.0=golang.org/x/net@v0.60.0"; \
    if [ "$TARGETARCH" = "arm" ] && [ -n "$TARGETVARIANT" ]; then \
      GOOS="$TARGETOS" \
      GOARCH="$TARGETARCH" \
      GOARM="${TARGETVARIANT#v}" \
        xcaddy build "v${CADDY_VERSION}" --with "$module" --replace "$net_replace"; \
    else \
      GOOS="$TARGETOS" \
      GOARCH="$TARGETARCH" \
        xcaddy build "v${CADDY_VERSION}" --with "$module" --replace "$net_replace"; \
    fi

FROM caddy:${CADDY_VERSION}-alpine

COPY --from=builder /usr/bin/caddy /usr/bin/caddy

RUN set -eux; \
    setcap -r /usr/bin/caddy; \
    addgroup -S -g 10001 caddy-runtime; \
    adduser -S -D -H -u 10001 -G caddy-runtime caddy-runtime; \
    mkdir -p /var/log/caddy; \
    chown -R caddy-runtime:caddy-runtime /config /data /var/log/caddy

USER 10001:10001
