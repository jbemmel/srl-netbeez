ARG SR_LINUX_RELEASE
FROM registry.srlinux.dev/pub/srlinux:$SR_LINUX_RELEASE

# Install Docker and docker-compose
RUN sudo yum install -y yum-utils && \
    sudo yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo && \
    sudo yum install -y docker-ce docker-ce-cli containerd.io && \
    sudo curl -L "https://github.com/docker/compose/releases/download/1.29.2/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose && \
    sudo chmod +x /usr/local/bin/docker-compose

RUN printf '%s\n' \
  '#!/bin/bash' \
  '' \
  'mkdir -p /etc/opt/srlinux/appmgr && cp /home/appmgr/* /etc/opt/srlinux/appmgr/' \
  'exit $?' \
  \
> /tmp/42.sh && sudo mv /tmp/42.sh /opt/srlinux/bin/bootscript/42_sr_copy_custom_appmgr.sh && \
  sudo chmod a+x /opt/srlinux/bin/bootscript/42_sr_copy_custom_appmgr.sh

# Preload the Netbeez Agent container
COPY netbeez_agent.tar.gz /

# Configure DNS for all containers -> breaks config save
# COPY etc_docker_daemon.json /etc/docker/daemon.json

# Add a binary from another image
# COPY --from=paris-traceroute /usr/local/bin/paris-* /usr/local/bin/

# COPY ./appmgr/ /etc/opt/srlinux/appmgr/ # doesn't work, using a script to copy at runtime
COPY ./appmgr/ /home/appmgr
