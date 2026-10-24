# syntax=docker/dockerfile:1
ARG NETBIRD_IMAGE=netbirdio/netbird:0.81.3@sha256:f8a4ea01da9f2351d5c9436209ee9caf35dd118fc9c1fc2ce7859f0c6e44781c
FROM ${NETBIRD_IMAGE} AS netbird
FROM debian:13-slim
ARG TARGETARCH
ARG TARGETVARIANT
ARG ZABBIX_VERSION=7.4
# arm/v7 is deliberately rejected until all packages AND gateway tests exist.
RUN case "$TARGETARCH/$TARGETVARIANT" in amd64/|arm64/|arm64/v8) ;; *) exit 64;; esac
RUN --mount=type=secret,id=ca,required=false \
    if test -f /run/secrets/ca; then printf 'Acquire::https::CaInfo "/run/secrets/ca";\n' >/etc/apt/apt.conf.d/99build-ca; fi; \
    printf '#!/bin/sh\nexit 101\n' >/usr/sbin/policy-rc.d && chmod 0755 /usr/sbin/policy-rc.d; \
    apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl python3 jq nftables iptables iproute2 iputils-ping \
      tini procps openssl sqlite3 socat tcpdump \
    && if test -f /run/secrets/ca; then export CURL_CA_BUNDLE=/run/secrets/ca; fi \
    && curl --fail --silent --show-error --location \
      "https://repo.zabbix.com/zabbix/${ZABBIX_VERSION}/release/debian/pool/main/z/zabbix-release/zabbix-release_latest_${ZABBIX_VERSION}+debian13_all.deb" -o /tmp/zabbix-release.deb \
    && dpkg -i /tmp/zabbix-release.deb && rm /tmp/zabbix-release.deb \
    && apt-get update && apt-get install -y --no-install-recommends \
      zabbix-proxy-sqlite3 zabbix-agent2 zabbix-sql-scripts \
    && for package in zabbix-proxy-sqlite3 zabbix-agent2 zabbix-sql-scripts; do \
         version=$(dpkg-query -W -f='${Version}' "$package"); \
         case "${version#*:}" in 7.4.*) ;; *) exit 65;; esac; \
       done \
    && dpkg-query -W -f='${Package}=${Version}\n' > /usr/share/openmain-packages.txt \
    && rm -rf /var/lib/apt/lists/* && rm -f /etc/apt/apt.conf.d/99build-ca /usr/sbin/policy-rc.d
COPY --from=netbird /usr/local/bin/netbird /usr/local/bin/netbird
# No native patches, lab client images, fault injectors or systemd installer.
COPY netbird-router/common/runtime.py netbird-router/common/network.py netbird-router/common/preflight.py netbird-router/common/zabbix_access.py netbird-router/common/zabbix_registration.py netbird-router/common/zabbix_sync.py /opt/openmain/netbird-router/common/
COPY zabbix-api-register.sh /opt/openmain/zabbix-api-register.sh
COPY docker/router/start.sh docker/router/router.py /opt/openmain/
RUN chmod 0755 /opt/openmain/start.sh && mkdir -p /run/zabbix \
    && chown zabbix:zabbix /run/zabbix \
    && netbird version && zabbix_agent2 -V && zabbix_proxy -V
ARG VCS_REF=unknown
LABEL org.opencontainers.image.source="https://github.com/Cobra97332/openmain-it-installer" \
      org.opencontainers.image.revision="${VCS_REF}"
ENV PYTHONDONTWRITEBYTECODE=1 NB_STATE_DIR=/var/lib/netbird \
    NB_DAEMON_ADDR=unix:///run/netbird.sock NB_WG_KERNEL_DISABLED=true
ENTRYPOINT ["/usr/bin/tini", "--", "/opt/openmain/start.sh"]
CMD ["gateway"]
