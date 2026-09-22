# Shared Makefile defaults, checks, safety gates, and help.
# Each module Makefile sets MODULE_ROOT and module-specific exceptions first.

SHELL := /bin/bash

MODULE_ROOT ?= $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
MODULE ?= $(notdir $(MODULE_ROOT))

NAMESPACE ?= $(MODULE)
HELM_RELEASE ?= $(MODULE)
HELM_CHART ?= helm
HELM_VALUES_BASE ?= -f $(HELM_CHART)/values.yaml
HELM_VALUES ?= $(HELM_VALUES_BASE)

IMAGE_REGISTRY ?=
IMAGE_REPOSITORY ?= $(MODULE)
IMAGE_TAG ?= 0.1.0
IMAGE := $(if $(IMAGE_REGISTRY),$(IMAGE_REGISTRY)/,)$(IMAGE_REPOSITORY):$(IMAGE_TAG)

CHART_REGISTRY ?= $(IMAGE_REGISTRY)
CHART_REPOSITORY ?= $(IMAGE_REPOSITORY)-chart
CHART_VERSION ?= $(IMAGE_TAG)
CHART_DIST_DIR ?= dist
CHART_PACKAGE ?= $(CHART_DIST_DIR)/$(CHART_REPOSITORY)-$(CHART_VERSION).tgz
CHART_OCI_REF ?= oci://$(CHART_REGISTRY)/$(CHART_REPOSITORY)
CHART_SOURCE ?= registry

HELM_EXTRA_ARGS ?=
FORCE ?= false
IMAGE_PUSH_FORCE ?= false
CHART_PUSH_FORCE ?= false
REQUIRED_TOOLS ?= kubectl helm

.PHONY: help-common
help-common:
	@echo "Common Make Targets and Variables"
	@echo "  make check-tools"
	@echo "    done: verify the module's required local CLI tools are installed"
	@echo "  make confirm-target-cluster"
	@echo "    done: display the target cluster and require confirmation before mutating operations"
	@echo "  MODULE=$(MODULE) MODULE_ROOT=$(MODULE_ROOT)"
	@echo "    done: identify the module and its Makefile directory; MODULE defaults to the directory name"
	@echo "  NAMESPACE=$(NAMESPACE)"
	@echo "    done: select the Kubernetes namespace; defaults to MODULE"
	@echo "  HELM_RELEASE=$(HELM_RELEASE) HELM_CHART=$(HELM_CHART)"
	@echo "    done: select the primary Helm release and chart path; both can be overridden per module or command line"
	@echo "  HELM_VALUES='$(HELM_VALUES)'"
	@echo "    done: select the primary chart values files; set ENV explicitly when layered values require it"
	@echo "  IMAGE_REGISTRY=$(IMAGE_REGISTRY) IMAGE_REPOSITORY=$(IMAGE_REPOSITORY) IMAGE_TAG=$(IMAGE_TAG)"
	@echo "    done: select image components; IMAGE is derived internally and is not a supported override"
	@echo "  CHART_REGISTRY=$(CHART_REGISTRY) CHART_REPOSITORY=$(CHART_REPOSITORY) CHART_VERSION=$(CHART_VERSION)"
	@echo "    done: select chart publication settings; repository defaults to IMAGE_REPOSITORY-chart"
	@echo "  CHART_SOURCE=$(CHART_SOURCE)"
	@echo "    done: choose registry (default) or local chart installation for apply workflows"
	@echo "  HELM_EXTRA_ARGS='$(HELM_EXTRA_ARGS)' FORCE=$(FORCE)"
	@echo "    done: pass extra Helm flags and optionally bypass cluster confirmation"
	@echo "  IMAGE_PUSH_FORCE=$(IMAGE_PUSH_FORCE) CHART_PUSH_FORCE=$(CHART_PUSH_FORCE)"
	@echo "    done: explicitly permit overwriting existing published image or chart versions"

.PHONY: check-tools
check-tools:
	@for tool in $(REQUIRED_TOOLS); do \
		$(MAKE) check-$$tool || exit $$?; \
	done

.PHONY: check-kubectl
check-kubectl:
	@command -v kubectl >/dev/null || (echo "kubectl not found" && exit 1)
	@echo "kubectl found: $$(command -v kubectl)"

.PHONY: check-helm
check-helm:
	@command -v helm >/dev/null || (echo "helm not found" && exit 1)
	@echo "helm found: $$(command -v helm)"

.PHONY: check-docker
check-docker:
	@command -v docker >/dev/null || (echo "docker not found" && exit 1)
	@echo "docker found: $$(command -v docker)"

.PHONY: check-skopeo
check-skopeo:
	@command -v skopeo >/dev/null || (echo "skopeo not found" && exit 1)
	@echo "skopeo found: $$(command -v skopeo)"

.PHONY: check-yq
check-yq:
	@command -v yq >/dev/null || (echo "yq not found" && exit 1)
	@echo "yq found: $$(command -v yq)"

.PHONY: confirm-target-cluster
confirm-target-cluster: check-kubectl
	@context=$$(kubectl config current-context 2>/dev/null || echo "<unknown>"); \
	cluster=$$(kubectl config view --minify -o 'jsonpath={.clusters[0].name}' 2>/dev/null || echo "<unknown>"); \
	server=$$(kubectl config view --minify -o 'jsonpath={.clusters[0].cluster.server}' 2>/dev/null || echo "<unknown>"); \
	user=$$(kubectl config view --minify -o 'jsonpath={.users[0].name}' 2>/dev/null || echo "<unknown>"); \
	echo "Target Kubernetes cluster:"; \
	echo "  context: $$context"; \
	echo "  cluster: $$cluster"; \
	echo "  server: $$server"; \
	echo "  user: $$user"; \
	echo "  target namespace: $(NAMESPACE)"; \
	if [[ -n "$(AUTH_NAMESPACE)" ]]; then echo "  auth namespace: $(AUTH_NAMESPACE)"; fi; \
	if [[ "$(FORCE)" =~ ^(1|true|yes|on)$$ ]]; then \
		echo "FORCE=true, bypassing confirmation."; \
		exit 0; \
	fi; \
	read -r -p "Proceed against this cluster? [y/N] " confirm; \
	if [[ ! "$$confirm" =~ ^[Yy]$$ ]]; then \
		echo "Aborted."; \
		exit 1; \
	fi
