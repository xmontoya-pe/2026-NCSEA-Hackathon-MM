FROM mcr.microsoft.com/devcontainers/python:3-3.14-trixie

# copy the umbrella CA being used for TLS inspection by Martin/Martin
COPY cisco-umbrella-root.crt /usr/local/share/ca-certificates/cisco-umbrella-root.crt
RUN update-ca-certificates

# have npm/Node ignore the OS trust store and use the TLS inspection cert
ENV NODE_EXTRA_CA_CERTS=/usr/local/share/ca-certificates/cisco-umbrella-root.crt