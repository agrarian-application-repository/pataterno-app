# ------------------------------------------------------------
# PATATERNO local satellite testbed - one image for all five nodes
# (sat, gw, st, ws_gw, ws_st), reproducing the NCSRD Testbed 1
# OpenSAND guide (v8.2.0) with containers instead of VirtualBox VMs.
#
# OpenSAND comes from the CNES net4sat package repository, exactly as
# in step 4 of the NCSRD Installation Guide (Ubuntu 22.04 "jammy").
# ------------------------------------------------------------
FROM ubuntu:22.04

ARG DEBIAN_FRONTEND=noninteractive
ARG OPENSAND_VERSION=8.2.0

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
 && mkdir -p /etc/apt/keyrings \
 && curl -fsSL https://raw.githubusercontent.com/CNES/net4sat-packages/master/gpg/net4sat.gpg.key \
    | gpg --dearmor -o /etc/apt/keyrings/net4sat.gpg \
 && printf 'Types: deb\nURIs: https://raw.githubusercontent.com/CNES/net4sat-packages/master/jammy/\nSuites: jammy\nComponents: stable\nSigned-By: /etc/apt/keyrings/net4sat.gpg\n' \
    > /etc/apt/sources.list.d/github.net4sat.sources \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      "opensand=${OPENSAND_VERSION}*" \
      iproute2 make iputils-ping iperf3 traceroute ethtool tcpdump \
      psmisc procps python3 \
 && rm -rf /var/lib/apt/lists/*

# The CNES "IP mode" configuration set (Makefiles + XML), unmodified.
# Interface names and the collector switch are passed as make variables
# at run time (see up.sh), so these files stay identical to upstream.
COPY opensand_cli/ip /opt/opensand_cli/ip

WORKDIR /opt/opensand_cli/ip
CMD ["sleep", "infinity"]
