SHELL := /bin/bash

# Configure core deployment defaults.
NAMESPACE ?= lm-serve
CATALOG_CONFIGMAP ?= lm-serve-model-catalog
CATALOG_KEY ?= models.yaml
EDGE_SERVICE_TYPE ?= LoadBalancer

# Configure optional image overrides for in-cluster reconciler and publisher.
DEPLOYER_IMAGE ?= REPLACE_ME_REGISTRY/vllm-catalog-deployer:0.1.0
PUBLISHER_IMAGE ?= REPLACE_ME_REGISTRY/lm-serve-model-publisher:0.1.0

# Configure Helm chart wiring and layered values model.
HELM_RELEASE ?= lm-serve
HELM_CHART ?= helm/lm-serve
HELM_BASE_VALUES ?= $(HELM_CHART)/values-base.yaml
ENV ?=
HELM_EXTRA_ARGS ?=
HELM_VALUES ?= -f $(HELM_BASE_VALUES) -f $(HELM_CHART)/values.$(ENV).yaml -f $(HELM_CHART)/values.$(ENV).models.yaml
HELM_UPGRADE_INSTALL ?= helm upgrade --install $(HELM_RELEASE) $(HELM_CHART) -n $(NAMESPACE) --create-namespace $(HELM_VALUES) $(HELM_EXTRA_ARGS)

# Configure auth and smoke-test inputs.
API_KEY ?=
JWT_TOKEN ?=
PROMPT ?= Reply with exactly the word OK.

.PHONY: help
help:
	@echo "LM Serve Make Targets"
	@echo ""
	@echo "Happy-path flow (bootstrap -> reconcile -> validate):"
	@echo "  make helm-template ENV=<env>"
	@echo "    done: render chart using values-base + values.<env> + values.<env>.models"
	@echo "  make helm-upgrade-base ENV=<env>"
	@echo "    done: apply base chart resources; disable publisher + reconciler"
	@echo "  make apply-base ENV=<env>"
	@echo "    done: run [helm-upgrade-base] + apply example secrets manifest"
	@echo "  make deploy-once"
	@echo "    done: reconcile catalog once; create/update model workloads + edge"
	@echo "  make generate-auth-materials"
	@echo "    done: generate local API key/JWT bootstrap test materials"
	@echo "  make smoke-api-key API_KEY=<k>"
	@echo "    done: validate all enabled models via API-key auth"
	@echo "  make smoke-jwt JWT_TOKEN=<t>"
	@echo "    done: validate all enabled models via JWT auth"
	@echo "  make smoke-both API_KEY=<k> JWT_TOKEN=<t>"
	@echo "    done: validate all enabled models via both auth modes"
	@echo "  make happy-path ..."
	@echo "    done: run [apply-base] -> [deploy-once] -> [smoke-api-key|smoke-jwt|smoke-both]"
	@echo ""
	@echo "Continuous reconciliation and automation options:"
	@echo "  make deploy-watch-local"
	@echo "    done: reconcile continuously from local machine every 60s"
	@echo "  make build-deployer-image"
	@echo "    done: build Script A in-cluster reconciler image"
	@echo "  make push-deployer-image"
	@echo "    done: push Script A in-cluster reconciler image"
	@echo "  make apply-model-reconciler ENV=<env> DEPLOYER_IMAGE=<img>"
	@echo "    done: enable modelReconciler and set controller image"
	@echo "  make build-model-publisher-image"
	@echo "    done: build Script B model publisher image"
	@echo "  make push-model-publisher-image"
	@echo "    done: push Script B model publisher image"
	@echo "  make apply-model-publisher-rbac ENV=<env>"
	@echo "    done: enable publisher RBAC resources"
	@echo "  make apply-model-publisher-cronjob ENV=<env> PUBLISHER_IMAGE=<img>"
	@echo "    done: enable publisher cronjob and set image"
	@echo ""
	@echo "Diagnostics and prerequisites:"
	@echo "  make check-tools"
	@echo "    done: verify kubectl + yq + docker are installed"
	@echo "  make check-helm"
	@echo "    done: verify helm is installed"
	@echo "  make require-env ENV=<env>"
	@echo "    done: verify layered values files for selected env"
	@echo ""
	@echo "Override variables (set as make VAR=value):"
	@echo "  NAMESPACE=$(NAMESPACE) CATALOG_CONFIGMAP=$(CATALOG_CONFIGMAP) CATALOG_KEY=$(CATALOG_KEY)"
	@echo "    done: select Kubernetes namespace and model catalog location"
	@echo "  DEPLOYER_IMAGE=$(DEPLOYER_IMAGE) PUBLISHER_IMAGE=$(PUBLISHER_IMAGE)"
	@echo "    done: set image references for reconciler and publisher automation"
	@echo "  ENV=<env-name> (required for Helm targets; example: ENV=sample-env)"
	@echo "    done: select values.<env>.yaml and values.<env>.models.yaml"
	@echo "  HELM_RELEASE=$(HELM_RELEASE) HELM_CHART=$(HELM_CHART) HELM_BASE_VALUES=$(HELM_BASE_VALUES) HELM_EXTRA_ARGS='$(HELM_EXTRA_ARGS)'"
	@echo "    done: override Helm release/chart/base values and pass extra Helm flags"

# Validate required local CLI tools.
.PHONY: check-tools
check-tools:
	@$(MAKE) check-kubectl
	@$(MAKE) check-yq
	@$(MAKE) check-docker

.PHONY: check-kubectl
check-kubectl:
	@command -v kubectl >/dev/null || (echo "kubectl not found" && exit 1)

.PHONY: check-yq
check-yq:
	@command -v yq >/dev/null || (echo "yq not found" && exit 1)

.PHONY: check-docker
check-docker:
	@command -v docker >/dev/null || (echo "docker not found" && exit 1)

.PHONY: check-helm
check-helm:
	@command -v helm >/dev/null || (echo "helm not found" && exit 1)

# Validate ENV and layered Helm values files.
.PHONY: require-env
require-env:
	@if [[ -z "$(ENV)" ]]; then echo "Set ENV=<env-name> (example: ENV=sample-env)"; exit 1; fi
	@test -f "$(HELM_BASE_VALUES)" || (echo "Missing $(HELM_BASE_VALUES)" && exit 1)
	@test -f "$(HELM_CHART)/values.$(ENV).yaml" || (echo "Missing $(HELM_CHART)/values.$(ENV).yaml" && exit 1)
	@test -f "$(HELM_CHART)/values.$(ENV).models.yaml" || (echo "Missing $(HELM_CHART)/values.$(ENV).models.yaml" && exit 1)

# Step 1: Bootstrap base chart resources and namespace secrets.
.PHONY: helm-template
helm-template: check-helm require-env
	helm template $(HELM_RELEASE) $(HELM_CHART) -n $(NAMESPACE) $(HELM_VALUES) $(HELM_EXTRA_ARGS)

.PHONY: helm-upgrade-base
helm-upgrade-base: check-helm check-kubectl require-env
	$(HELM_UPGRADE_INSTALL) \
	  --set modelPublisherCronjob.enabled=false \
	  --set modelReconciler.enabled=false

.PHONY: apply-base
apply-base: helm-upgrade-base
	kubectl -n $(NAMESPACE) apply -f manifests/secrets.examples.yaml

# Step 2: Reconcile model catalog into running model workloads and edge components.
.PHONY: deploy-once
deploy-once: check-kubectl check-yq
	bash deploy/deploy_lm_serve_catalog.sh \
	  --namespace $(NAMESPACE) \
	  --catalog-configmap $(CATALOG_CONFIGMAP) \
	  --catalog-key $(CATALOG_KEY) \
	  --edge-service-type $(EDGE_SERVICE_TYPE)

.PHONY: deploy-watch-local
deploy-watch-local: check-kubectl check-yq
	bash deploy/deploy_lm_serve_catalog.sh \
	  --namespace $(NAMESPACE) \
	  --catalog-configmap $(CATALOG_CONFIGMAP) \
	  --catalog-key $(CATALOG_KEY) \
	  --edge-service-type $(EDGE_SERVICE_TYPE) \
	  --watch-interval-sec 60

# Step 3: Generate test credentials and validate serving with smoke tests.
.PHONY: generate-auth-materials
generate-auth-materials:
	bash deploy/generate_test_auth_materials.sh --team-size 3 --jwt-sub dev-user --jwt-ttl-sec 3600

.PHONY: smoke-api-key
smoke-api-key: check-kubectl check-yq
	@if [[ -z "$(API_KEY)" ]]; then echo "Set API_KEY variable"; exit 1; fi
	bash deploy/smoke_test_lm_serve_catalog.sh \
	  --namespace $(NAMESPACE) \
	  --catalog-configmap $(CATALOG_CONFIGMAP) \
	  --catalog-key $(CATALOG_KEY) \
	  --api-key "$(API_KEY)" \
	  --prompt "$(PROMPT)"

.PHONY: smoke-jwt
smoke-jwt: check-kubectl check-yq
	@if [[ -z "$(JWT_TOKEN)" ]]; then echo "Set JWT_TOKEN variable"; exit 1; fi
	bash deploy/smoke_test_lm_serve_catalog.sh \
	  --namespace $(NAMESPACE) \
	  --catalog-configmap $(CATALOG_CONFIGMAP) \
	  --catalog-key $(CATALOG_KEY) \
	  --jwt-token "$(JWT_TOKEN)" \
	  --prompt "$(PROMPT)"

.PHONY: smoke-both
smoke-both: check-kubectl check-yq
	@if [[ -z "$(API_KEY)" ]]; then echo "Set API_KEY variable"; exit 1; fi
	@if [[ -z "$(JWT_TOKEN)" ]]; then echo "Set JWT_TOKEN variable"; exit 1; fi
	bash deploy/smoke_test_lm_serve_catalog.sh \
	  --namespace $(NAMESPACE) \
	  --catalog-configmap $(CATALOG_CONFIGMAP) \
	  --catalog-key $(CATALOG_KEY) \
	  --api-key "$(API_KEY)" \
	  --jwt-token "$(JWT_TOKEN)" \
	  --prompt "$(PROMPT)"

# Step 4: Compose and run the end-to-end happy path.
.PHONY: happy-path
happy-path:
	@if [[ -z "$(API_KEY)" && -z "$(JWT_TOKEN)" ]]; then echo "Set API_KEY and/or JWT_TOKEN for smoke validation"; exit 1; fi
	@echo "[1/4] Ensuring namespace and base manifests exist."
	@echo "      Why: catalog and secrets must exist before reconciliation can render model workloads."
	@$(MAKE) apply-base
	@echo "[2/4] Reconciling catalog to create/update vLLM model StatefulSets and Envoy edge."
	@echo "      Why: this materializes desired model state from the shared ConfigMap."
	@$(MAKE) deploy-once
	@echo "[3/4] Running smoke test against enabled models through the edge endpoint."
	@echo "      Why: validates auth path plus basic inference availability after deployment."
	@if [[ -n "$(API_KEY)" && -n "$(JWT_TOKEN)" ]]; then \
		$(MAKE) smoke-both API_KEY="$(API_KEY)" JWT_TOKEN="$(JWT_TOKEN)" PROMPT="$(PROMPT)"; \
	elif [[ -n "$(API_KEY)" ]]; then \
		$(MAKE) smoke-api-key API_KEY="$(API_KEY)" PROMPT="$(PROMPT)"; \
	else \
		$(MAKE) smoke-jwt JWT_TOKEN="$(JWT_TOKEN)" PROMPT="$(PROMPT)"; \
	fi
	@echo "[4/4] Happy path complete."
	@echo "      What next: use deploy-watch-local or apply-model-reconciler for continuous model reconciliation."

# Optional extension: Automate in-cluster model reconciliation.
.PHONY: build-deployer-image
build-deployer-image: check-docker
	docker build -t $(DEPLOYER_IMAGE) deploy

.PHONY: push-deployer-image
push-deployer-image: check-docker
	docker push $(DEPLOYER_IMAGE)

.PHONY: apply-model-reconciler
apply-model-reconciler: check-helm check-kubectl require-env
	$(HELM_UPGRADE_INSTALL) \
	  --set modelReconciler.enabled=true \
	  --set-string modelReconciler.image=$(DEPLOYER_IMAGE)

# Optional extension: Automate model publishing.
.PHONY: build-model-publisher-image
build-model-publisher-image: check-docker
	docker build -t $(PUBLISHER_IMAGE) publisher

.PHONY: push-model-publisher-image
push-model-publisher-image: check-docker
	docker push $(PUBLISHER_IMAGE)

.PHONY: apply-model-publisher-rbac
apply-model-publisher-rbac: check-helm check-kubectl require-env
	$(HELM_UPGRADE_INSTALL) \
	  --set modelPublisherRbac.enabled=true

.PHONY: apply-model-publisher-cronjob
apply-model-publisher-cronjob: check-helm check-kubectl require-env
	$(HELM_UPGRADE_INSTALL) \
	  --set modelPublisherCronjob.enabled=true \
	  --set-string modelPublisherCronjob.image=$(PUBLISHER_IMAGE)
