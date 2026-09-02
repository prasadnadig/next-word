# Runtime image for running Script A as an in-cluster model reconciler.
FROM alpine:3.21

ARG KUBECTL_VERSION=v1.32.3

RUN apk add --no-cache bash ca-certificates curl yq \
  && curl -fsSL -o /usr/local/bin/kubectl \
     "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
  && chmod +x /usr/local/bin/kubectl \
  && addgroup -S app \
  && adduser -S -D -H -h /app -u 10001 -G app app

WORKDIR /app
COPY deploy_lm_serve_catalog.sh /app/deploy_lm_serve_catalog.sh
RUN chmod +x /app/deploy_lm_serve_catalog.sh

USER app
ENTRYPOINT ["/app/deploy_lm_serve_catalog.sh"]
