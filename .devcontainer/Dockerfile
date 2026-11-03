FROM mcr.microsoft.com/devcontainers/python:3-3.14-trixie

# optional: trust any extra root certificates (*.crt) placed in .devcontainer/; none are required
# (Dockerfile is listed so the COPY still succeeds when there are no .crt files)
COPY Dockerfile *.crt /tmp/extra-certs/
RUN find /tmp/extra-certs -name '*.crt' -exec cp {} /usr/local/share/ca-certificates/ \; \
    && update-ca-certificates \
    && rm -rf /tmp/extra-certs

# have npm/Node use the system trust store, including any certificates added above
ENV NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt
