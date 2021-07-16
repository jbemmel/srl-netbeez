ARG SR_LINUX_RELEASE
FROM registry.srlinux.dev/pub/srlinux:$SR_LINUX_RELEASE

RUN printf '%s\n' \
  '#!/bin/bash' \
  '' \
  'mkdir -p /etc/opt/srlinux/appmgr && cp /home/appmgr/* /etc/opt/srlinux/appmgr/' \
  'echo "nameserver 8.8.8.8" > /etc/resolv.conf' \
  'sudo /usr/bin/dockerd &' \
  'exit $?' \
  \
> /tmp/42.sh && sudo mv /tmp/42.sh /opt/srlinux/bin/bootscript/42_sr_copy_custom_appmgr.sh && \
  sudo chmod a+x /opt/srlinux/bin/bootscript/42_sr_copy_custom_appmgr.sh

# Install Docker and docker-compose
RUN sudo yum install -y yum-utils && \
    sudo yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo && \
    sudo yum install -y docker-ce docker-ce-cli containerd.io && \
    sudo curl -L "https://github.com/docker/compose/releases/download/1.29.2/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose && \
    sudo chmod +x /usr/local/bin/docker-compose

# Install pyGNMI to /usr/local/lib[64]/python3.6/site-packages
RUN sudo yum install -y python3-pip gcc-c++ && \
    sudo python3 -m pip install pip --upgrade && \
    sudo python3 -m pip install pygnmi

# Preload the Netbeez Agent container
COPY netbeez_agent.tar.gz /

# --chown=srlinux:srlinux
# COPY ./appmgr/ /etc/opt/srlinux/appmgr/ # doesn't stick
COPY ./appmgr/ /home/appmgr

# Using a build arg to set the release tag, set a default for running docker build manually
ARG SRL_AUTO_CONFIG_RELEASE="[custom build]"
ENV SRL_AUTO_CONFIG_RELEASE=$SRL_AUTO_CONFIG_RELEASE
