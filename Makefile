NAME        := srl/netbeez-agent
LAST_COMMIT := $(shell sh -c "git log -1 --pretty=%h")
TODAY       := $(shell sh -c "date +%Y%m%d_%H%M")
TAG         := ${TODAY}.${LAST_COMMIT}
IMG         := ${NAME}:${TAG}

# HTTP_PROXY  := "http://proxy.lbs.alcatel-lucent.com:8000"

ifndef SR_LINUX_RELEASE
override SR_LINUX_RELEASE="latest"
endif

build:
	echo "Building agent netbeez-agent for ${SR_LINUX_RELEASE}..."
	docker pull netbeez/nb-agent
	docker save netbeez/nb-agent | gzip > netbeez_agent.tar.gz
	docker build --build-arg SRL_AUTO_CONFIG_RELEASE=${TAG} \
	  --build-arg http_proxy=${HTTP_PROXY} --build-arg https_proxy=${HTTP_PROXY} \
	  --build-arg SR_LINUX_RELEASE="${SR_LINUX_RELEASE}" \
		-f Dockerfile -t ${IMG} .
	docker tag ${IMG} ${NAME}:${SR_LINUX_RELEASE}
