SHELL := /bin/bash

# Configure core deployment defaults.
NAMESPACE ?= lm-serve
CATALOG_CONFIGMAP ?= lm-serve-model-catalog
CATALOG_KEY ?= models.yaml
MODEL_SERVICE_TYPE ?= LoadBalancer
AUTH_NAMESPACE ?= $(if $(ENV),$(ENV),$(NAMESPACE))

# Configure optional image overrides for in-cluster model reconciliation and publishing.
AUTH_IMAGE ?= REPLACE_ME_REGISTRY/lm-serve-auth-service:0.1.0
MODEL_RECONCILER_IMAGE ?= REPLACE_ME_REGISTRY/vllm-catalog-deployer:0.1.0
PUBLISHER_IMAGE ?= REPLACE_ME_REGISTRY/lm-serve-model-publisher:0.1.0

# Configure Helm chart wiring and layered values model.
AUTH_HELM_RELEASE ?= lm-serve-auth
AUTH_HELM_CHART ?= helm/lm-serve-auth
AUTH_HELM_VALUES ?= -f $(AUTH_HELM_CHART)/values.yaml -f $(AUTH_HELM_CHART)/values.$(ENV).yaml

PLATFORM_HELM_RELEASE ?= lm-serve-platform
PLATFORM_HELM_CHART ?= helm/lm-serve-platform
PLATFORM_HELM_VALUES ?= -f $(PLATFORM_HELM_CHART)/values.yaml

MODELS_HELM_RELEASE ?= lm-serve-models
MODELS_HELM_CHART ?= helm/lm-serve-models
MODELS_HELM_VALUES ?= -f $(MODELS_HELM_CHART)/values.yaml -f $(MODELS_HELM_CHART)/values.$(ENV).yaml

PUBLISHER_HELM_RELEASE ?= lm-serve-publisher
PUBLISHER_HELM_CHART ?= helm/lm-serve-publisher
PUBLISHER_HELM_VALUES ?= -f $(PUBLISHER_HELM_CHART)/values.yaml

ENV ?=
HELM_EXTRA_ARGS ?=

# Configure auth and smoke-test inputs.
API_KEY ?=
JWT_TOKEN ?=
PROMPT ?= Reply with exactly the word OK.

.PHONY: help
help:
	@echo "LM Serve Make Targets"
	@echo ""
	@echo "Primary flow (install split charts -> reconcile -> validate):"
	@echo "  make render-helm-template ENV=sample-env"
	@echo "    done: render auth, platform, models, and publisher charts with their chart-local defaults"
	@echo "    done+: validate the split chart surfaces before installing them"
	@echo "  make apply-base ENV=sample-env"
	@echo "    done: install auth, platform, and models charts; disable the model reconciler Job for the base install"
	@echo "    done+: apply namespace-scoped example Secrets required by auth/object-storage workflows"
	@echo "  make deploy-once"
	@echo "    done: reconcile catalog once; create/update model StatefulSets, related Services, and route data"
	@echo "  make generate-auth-materials"
	@echo "    done: generate bootstrap API key/JWT materials for small-team validation"
	@echo "  make smoke-test-api-key API_KEY=<k>"
	@echo "    done: validate edge auth + baseline inference for all enabled models via API-key flow"
	@echo "  make smoke-test-jwt JWT_TOKEN=<t>"
	@echo "    done: validate edge auth + baseline inference for all enabled models via JWT flow"
	@echo "  make smoke-test-all API_KEY=<k> JWT_TOKEN=<t>"
	@echo "    done: validate all configured auth paths and baseline inference for all enabled models"
	@echo "  make happy-path ..."
	@echo "    done: run [apply-base] -> [deploy-once] -> [smoke-test-api-key|smoke-test-jwt|smoke-test-all]"
	@echo "    done+: produces a validated baseline LM Serve deployment from split charts + catalog + auth test"
	@echo ""
	@echo "Continuous reconciliation and automation options:"
	@echo "  make deploy-watch-local"
	@echo "    done: reconcile continuously from local machine every 60s to keep workloads aligned with catalog edits"
	@echo "  make build-model-reconciler-image"
	@echo "    done: build model deployment reconciler container image"
	@echo "  make push-model-reconciler-image"
	@echo "    done: push model deployment reconciler image to the specified container registry"
	@echo "  make build-auth-image"
	@echo "    done: build auth service image from tracked source"
	@echo "  make push-auth-image"
	@echo "    done: push auth service image to the specified container registry"
	@echo "  make apply-auth-service"
	@echo "    done: deploy or update auth chart with the selected auth image"
	@echo "  make apply-model-reconciler ENV=<env> MODEL_RECONCILER_IMAGE=<img>"
	@echo "    done: enable in-cluster model reconciliation and configure the reconciler image used by the Job"
	@echo "  make build-model-publisher-image"
	@echo "    done: build model catalog publisher image"
	@echo "  make push-model-publisher-image"
	@echo "    done: push model catalog publisher image to the specified container registry"
	@echo "  make apply-model-publisher PUBLISHER_IMAGE=<img>"
	@echo "    done: install the publisher chart in its dedicated namespace; ENV is not required because the catalog registry is defined in values.yaml"
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
	@echo "  AUTH_NAMESPACE=$(AUTH_NAMESPACE)"
	@echo "    done: select where auth chart is deployed; default follows ENV, override for shared auth"
	@echo "  AUTH_IMAGE=$(AUTH_IMAGE) MODEL_RECONCILER_IMAGE=$(MODEL_RECONCILER_IMAGE) PUBLISHER_IMAGE=$(PUBLISHER_IMAGE)"
	@echo "    done: set runtime images for auth service, model reconciliation, and model publishing automation"
	@echo "  ENV=<env-name> (required for Helm targets; example: ENV=sample-env)"
	@echo "    done: select values.<env>.yaml for the consumer-side model chart and the publisher catalog for that env"
	@echo "  AUTH_HELM_RELEASE=$(AUTH_HELM_RELEASE) PLATFORM_HELM_RELEASE=$(PLATFORM_HELM_RELEASE) MODELS_HELM_RELEASE=$(MODELS_HELM_RELEASE) PUBLISHER_HELM_RELEASE=$(PUBLISHER_HELM_RELEASE)"
	@echo "    done: override chart release names"
	@echo "  AUTH_HELM_CHART=$(AUTH_HELM_CHART) PLATFORM_HELM_CHART=$(PLATFORM_HELM_CHART) MODELS_HELM_CHART=$(MODELS_HELM_CHART) PUBLISHER_HELM_CHART=$(PUBLISHER_HELM_CHART)"
	@echo "    done: override chart locations"
	@echo "  ENV=<env-name> (required for models chart values; example: ENV=sample-env)"
	@echo "    done: select values.<env>.yaml for the model consumer chart; source catalog intent lives under helm/lm-serve-publisher/catalogs/<env>.yaml"
	@echo "  HELM_EXTRA_ARGS='$(HELM_EXTRA_ARGS)'"
	@echo "    done: pass extra Helm flags to chart install/render commands"

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
	@test -f "$(MODELS_HELM_CHART)/values.yaml" || (echo "Missing $(MODELS_HELM_CHART)/values.yaml" && exit 1)
	@test -f "$(MODELS_HELM_CHART)/values.$(ENV).yaml" || (echo "Missing $(MODELS_HELM_CHART)/values.$(ENV).yaml" && exit 1)
	@test -f "$(PUBLISHER_HELM_CHART)/catalogs/$(ENV).yaml" || (echo "Missing $(PUBLISHER_HELM_CHART)/catalogs/$(ENV).yaml; source catalog intent lives under the publisher chart" && exit 1)
	@test -f "$(AUTH_HELM_CHART)/values.$(ENV).yaml" || (echo "Missing $(AUTH_HELM_CHART)/values.$(ENV).yaml" && exit 1)

# Step 1: Bootstrap base chart resources and namespace secrets.
.PHONY: render-helm-template
render-helm-template: check-helm require-env
	helm template $(AUTH_HELM_RELEASE) $(AUTH_HELM_CHART) -n $(AUTH_NAMESPACE) $(AUTH_HELM_VALUES) --set-string authService.image=$(AUTH_IMAGE) $(HELM_EXTRA_ARGS)
	helm template $(PLATFORM_HELM_RELEASE) $(PLATFORM_HELM_CHART) -n $(NAMESPACE) $(PLATFORM_HELM_VALUES) --set authz.endpoint.namespace=$(AUTH_NAMESPACE) $(HELM_EXTRA_ARGS)
	helm template $(MODELS_HELM_RELEASE) $(MODELS_HELM_CHART) -n $(NAMESPACE) $(MODELS_HELM_VALUES) $(HELM_EXTRA_ARGS)
	helm template $(PUBLISHER_HELM_RELEASE) $(PUBLISHER_HELM_CHART) -n $(NAMESPACE) $(PUBLISHER_HELM_VALUES) $(HELM_EXTRA_ARGS)

.PHONY: helm-upgrade-base
helm-upgrade-base: check-helm check-kubectl require-env
	helm upgrade --install $(AUTH_HELM_RELEASE) $(AUTH_HELM_CHART) -n $(AUTH_NAMESPACE) --create-namespace $(AUTH_HELM_VALUES) --set-string authService.image=$(AUTH_IMAGE) $(HELM_EXTRA_ARGS)
	helm upgrade --install $(PLATFORM_HELM_RELEASE) $(PLATFORM_HELM_CHART) -n $(NAMESPACE) --create-namespace $(PLATFORM_HELM_VALUES) --set authz.endpoint.namespace=$(AUTH_NAMESPACE) $(HELM_EXTRA_ARGS)
	helm upgrade --install $(MODELS_HELM_RELEASE) $(MODELS_HELM_CHART) -n $(NAMESPACE) --create-namespace $(MODELS_HELM_VALUES) --set modelReconciler.enabled=false $(HELM_EXTRA_ARGS)

.PHONY: apply-base
apply-base: helm-upgrade-base
	kubectl -n $(NAMESPACE) apply -f manifests/secrets.examples.yaml
	@if [[ "$(AUTH_NAMESPACE)" != "$(NAMESPACE)" ]]; then \
		kubectl -n $(AUTH_NAMESPACE) apply -f manifests/secrets.examples.yaml; \
	fi

# Step 2: Reconcile model catalog into running model workloads and services.
.PHONY: deploy-once
deploy-once: check-kubectl check-yq
	bash deploy/deploy_lm_serve_catalog.sh \
	  --namespace $(NAMESPACE) \
	  --catalog-configmap $(CATALOG_CONFIGMAP) \
	  --catalog-key $(CATALOG_KEY) \
	  --model-service-type $(MODEL_SERVICE_TYPE)

.PHONY: deploy-watch-local
deploy-watch-local: check-kubectl check-yq
	bash deploy/deploy_lm_serve_catalog.sh \
	  --namespace $(NAMESPACE) \
	  --catalog-configmap $(CATALOG_CONFIGMAP) \
	  --catalog-key $(CATALOG_KEY) \
	  --model-service-type $(MODEL_SERVICE_TYPE) \
	  --watch-interval-sec 60

# Step 3: Generate test credentials and validate serving with smoke tests.
.PHONY: generate-auth-materials
generate-auth-materials:
	bash deploy/generate_test_auth_materials.sh --team-size 3 --jwt-sub dev-user --jwt-ttl-sec 3600

.PHONY: smoke-test-api-key
smoke-test-api-key: check-kubectl check-yq
	@if [[ -z "$(API_KEY)" ]]; then echo "Set API_KEY variable"; exit 1; fi
	bash deploy/smoke_test_lm_serve_catalog.sh \
	  --namespace $(NAMESPACE) \
	  --catalog-configmap $(CATALOG_CONFIGMAP) \
	  --catalog-key $(CATALOG_KEY) \
	  --api-key "$(API_KEY)" \
	  --prompt "$(PROMPT)"

.PHONY: smoke-test-jwt
smoke-test-jwt: check-kubectl check-yq
	@if [[ -z "$(JWT_TOKEN)" ]]; then echo "Set JWT_TOKEN variable"; exit 1; fi
	bash deploy/smoke_test_lm_serve_catalog.sh \
	  --namespace $(NAMESPACE) \
	  --catalog-configmap $(CATALOG_CONFIGMAP) \
	  --catalog-key $(CATALOG_KEY) \
	  --jwt-token "$(JWT_TOKEN)" \
	  --prompt "$(PROMPT)"

.PHONY: smoke-test-all
smoke-test-all: check-kubectl check-yq
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
		$(MAKE) smoke-test-all API_KEY="$(API_KEY)" JWT_TOKEN="$(JWT_TOKEN)" PROMPT="$(PROMPT)"; \
	elif [[ -n "$(API_KEY)" ]]; then \
		$(MAKE) smoke-test-api-key API_KEY="$(API_KEY)" PROMPT="$(PROMPT)"; \
	else \
		$(MAKE) smoke-test-jwt JWT_TOKEN="$(JWT_TOKEN)" PROMPT="$(PROMPT)"; \
	fi
	@echo "[4/4] Happy path complete."
	@echo "      What next: use deploy-watch-local or apply-model-reconciler for continuous model reconciliation."

# Optional extension: Automate in-cluster model reconciliation.
.PHONY: build-auth-image
build-auth-image: check-docker
	docker build -t $(AUTH_IMAGE) auth-service

.PHONY: push-auth-image
push-auth-image: check-docker
	docker push $(AUTH_IMAGE)

.PHONY: apply-auth-service
apply-auth-service: check-helm check-kubectl
	helm upgrade --install $(AUTH_HELM_RELEASE) $(AUTH_HELM_CHART) -n $(AUTH_NAMESPACE) --create-namespace $(AUTH_HELM_VALUES) --set-string authService.image=$(AUTH_IMAGE) $(HELM_EXTRA_ARGS)

# Optional extension: Automate in-cluster model reconciliation.
.PHONY: build-model-reconciler-image
build-model-reconciler-image: check-docker
	docker build -t $(MODEL_RECONCILER_IMAGE) deploy

.PHONY: push-model-reconciler-image
push-model-reconciler-image: check-docker
	docker push $(MODEL_RECONCILER_IMAGE)

.PHONY: apply-model-reconciler
apply-model-reconciler: check-helm check-kubectl require-env
	helm upgrade --install $(MODELS_HELM_RELEASE) $(MODELS_HELM_CHART) -n $(NAMESPACE) --create-namespace $(MODELS_HELM_VALUES) \
	  --set modelReconciler.enabled=true \
	  --set-string modelReconciler.image=$(MODEL_RECONCILER_IMAGE) $(HELM_EXTRA_ARGS)

# Optional extension: Automate model publishing.
.PHONY: build-model-publisher-image
build-model-publisher-image: check-docker
	docker build -t $(PUBLISHER_IMAGE) publisher

.PHONY: push-model-publisher-image
push-model-publisher-image: check-docker
	docker push $(PUBLISHER_IMAGE)

.PHONY: apply-model-publisher
apply-model-publisher: check-helm check-kubectl
	helm upgrade --install $(PUBLISHER_HELM_RELEASE) $(PUBLISHER_HELM_CHART) -n $(NAMESPACE) --create-namespace $(PUBLISHER_HELM_VALUES) \
	  --set publisher.enabled=true --set consumer.enabled=true \
	  --set-string publisher.image=$(PUBLISHER_IMAGE) $(HELM_EXTRA_ARGS)
