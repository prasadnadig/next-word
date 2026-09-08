SHELL := /bin/bash

# Configure core deployment defaults.
NAMESPACE ?= lm-serve
CATALOG_KEY ?= models.yaml
ROUTE_CONFIGMAP ?= lm-serve-envoy-routes
ROUTE_KEY ?= routes.yaml
MODEL_SERVICE_TYPE ?= LoadBalancer
AUTH_NAMESPACE ?= $(if $(TENANT),$(TENANT),$(NAMESPACE))

# Configure optional image overrides for in-cluster model reconciliation and publishing.
AUTH_IMAGE ?= REPLACE_ME_REGISTRY/lm-serve-auth-service:0.1.0
MODEL_RECONCILER_IMAGE ?= REPLACE_ME_REGISTRY/vllm-catalog-deployer:0.1.0
MODEL_RECONCILER_JOB_NAME ?= vllm-catalog-model-reconciler
MODEL_RECONCILER_WATCH_INTERVAL_SEC ?= 60
PUBLISHER_IMAGE ?= REPLACE_ME_REGISTRY/lm-serve-model-publisher:0.1.0
ROUTE_AGGREGATOR_IMAGE_REPOSITORY ?= REPLACE_ME_REGISTRY/lm-serve-route-aggregator
ROUTE_AGGREGATOR_IMAGE_TAG ?= 0.1.0
ROUTE_AGGREGATOR_IMAGE ?= $(ROUTE_AGGREGATOR_IMAGE_REPOSITORY):$(ROUTE_AGGREGATOR_IMAGE_TAG)

# Configure Helm chart wiring and layered values model.
AUTH_HELM_RELEASE ?= lm-serve-auth
AUTH_HELM_CHART ?= helm/lm-serve-auth
AUTH_HELM_VALUES_BASE ?= -f $(AUTH_HELM_CHART)/values.yaml
AUTH_HELM_VALUES ?= $(AUTH_HELM_VALUES_BASE)

PLATFORM_HELM_RELEASE ?= lm-serve-platform
PLATFORM_HELM_CHART ?= helm/lm-serve-platform
PLATFORM_HELM_VALUES ?= -f $(PLATFORM_HELM_CHART)/values.yaml

MODELS_HELM_RELEASE ?= lm-serve-models
MODELS_HELM_CHART ?= helm/lm-serve-models
MODELS_HELM_VALUES_BASE ?= -f $(MODELS_HELM_CHART)/values.yaml
MODELS_HELM_VALUES_ENV ?= -f $(MODELS_HELM_CHART)/values.$(TENANT).yaml
MODELS_HELM_VALUES ?= $(MODELS_HELM_VALUES_BASE) $(if $(TENANT),$(MODELS_HELM_VALUES_ENV),)

PUBLISHER_HELM_RELEASE ?= lm-serve-publisher
PUBLISHER_HELM_CHART ?= helm/lm-serve-publisher
PUBLISHER_HELM_VALUES ?= -f $(PUBLISHER_HELM_CHART)/values.yaml

TENANT ?=
HELM_EXTRA_ARGS ?=
FORCE ?= false
PUBLISH_IF_MISSING ?= false

# Configure auth and smoke-test inputs.
API_KEY ?=
JWT_TOKEN ?=
PROMPT ?= Reply with exactly the word OK.

.PHONY: help
help:
	@echo "LM Serve Make Targets"
	@echo ""
	@echo "Primary flow (install split charts -> reconcile -> validate):"
	@echo "  make render-helm-template [TENANT=sample-env]"
	@echo "    done: render auth, platform, models, and publisher charts with chart-local defaults; when TENANT is set, include models tenant overlays"
	@echo "    done+: validate the split chart surfaces before installing them"
	@echo "  make apply-base TENANT=sample-env"
	@echo "    done: install auth, platform, and models charts; disable the model reconciler Job for the base install"
	@echo "    done+: apply namespace-scoped example Secrets required by auth/object-storage workflows"
	@echo "  make apply-serve-model"
	@echo "    done: run one in-cluster reconciliation pass via the model-reconciler image and wait for Job completion"
	@echo "  make generate-auth-materials"
	@echo "    done: generate bootstrap API key/JWT materials for small-team validation"
	@echo "  make smoke-test-api-key API_KEY=<k>"
	@echo "    done: validate edge auth + baseline inference for all enabled models via API-key flow"
	@echo "  make smoke-test-jwt JWT_TOKEN=<t>"
	@echo "    done: validate edge auth + baseline inference for all enabled models via JWT flow"
	@echo "  make smoke-test-all API_KEY=<k> JWT_TOKEN=<t>"
	@echo "    done: validate all configured auth paths and baseline inference for all enabled models"
	@echo "  make happy-build TENANT=sample-env"
	@echo "    done: render all Helm charts and build all local Docker images required by LM Serve components"
	@echo "  make happy-publish"
	@echo "    done: push all LM Serve Docker images to their configured registries; set PUBLISH_IF_MISSING=true to skip images already present"
	@echo "  make happy-release ..."
	@echo "    done: run [happy-build] -> [happy-publish] -> [happy-apply] as a single release workflow"
	@echo "  make happy-apply ..."
	@echo "    done: run [apply-base] -> [apply-route-aggregator] -> [apply-model-reconciler] -> [apply-serve-model] -> [smoke-test-*]"
	@echo "    done+: installs required software to the target cluster in dependency-safe order and validates edge inference"
	@echo ""
	@echo "Continuous reconciliation and automation options:"
	@echo "  make deploy-watch-local"
	@echo "    done: enable in-cluster continuous reconciliation using the model-reconciler image"
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
	@echo "  make apply-model-reconciler TENANT=<tenant> MODEL_RECONCILER_IMAGE=<img>"
	@echo "    done: enable in-cluster model reconciliation and configure Job/image/args for the reconciler"
	@echo "  make build-route-aggregator-image"
	@echo "    done: build standalone lm-serve-platform route-aggregator controller image"
	@echo "  make push-route-aggregator-image"
	@echo "    done: push standalone route-aggregator controller image to the specified container registry"
	@echo "  make apply-route-aggregator ROUTE_AGGREGATOR_IMAGE_REPOSITORY=<repo> ROUTE_AGGREGATOR_IMAGE_TAG=<tag>"
	@echo "    done: enable platform live route updates and set the route-aggregator controller image repository and tag"
	@echo "  make build-model-publisher-image"
	@echo "    done: build model catalog publisher image"
	@echo "  make push-model-publisher-image"
	@echo "    done: push model catalog publisher image to the specified container registry"
	@echo "  make apply-model-publisher PUBLISHER_IMAGE=<img>"
	@echo "    done: install the publisher chart in its dedicated namespace; TENANT is not required because the catalog registry is defined in values.yaml"
	@echo ""
	@echo "Diagnostics and prerequisites:"
	@echo "  make check-tools"
	@echo "    done: verify kubectl + yq + docker are installed"
	@echo "  make check-helm"
	@echo "    done: verify helm is installed"
	@echo "  make require-tenant TENANT=<tenant>"
	@echo "    done: verify TENANT variable is set"
	@echo "  make require-tenant-models TENANT=<tenant>"
	@echo "    done: verify models tenant overlay exists"
	@echo ""
	@echo "Override variables (set as make VAR=value):"
	@echo "  NAMESPACE=$(NAMESPACE) CATALOG_KEY=$(CATALOG_KEY) ROUTE_CONFIGMAP=$(ROUTE_CONFIGMAP) ROUTE_KEY=$(ROUTE_KEY)"
	@echo "    done: select Kubernetes namespace and model catalog location"
	@echo "  AUTH_NAMESPACE=$(AUTH_NAMESPACE)"
	@echo "    done: select where auth chart is deployed; default follows TENANT when set, otherwise NAMESPACE"
	@echo "  AUTH_IMAGE=$(AUTH_IMAGE) MODEL_RECONCILER_IMAGE=$(MODEL_RECONCILER_IMAGE) PUBLISHER_IMAGE=$(PUBLISHER_IMAGE)"
	@echo "    done: set runtime images for auth service, model reconciliation, and model publishing automation"
	@echo "  MODEL_RECONCILER_JOB_NAME=$(MODEL_RECONCILER_JOB_NAME) MODEL_RECONCILER_WATCH_INTERVAL_SEC=$(MODEL_RECONCILER_WATCH_INTERVAL_SEC)"
	@echo "    done: override in-cluster reconciler Job name and watch interval seconds"
	@echo "  ROUTE_AGGREGATOR_IMAGE=$(ROUTE_AGGREGATOR_IMAGE)"
	@echo "    done: set standalone platform route-aggregator image (build/push/apply-route-aggregator)"
	@echo "  ROUTE_AGGREGATOR_IMAGE_REPOSITORY=$(ROUTE_AGGREGATOR_IMAGE_REPOSITORY) ROUTE_AGGREGATOR_IMAGE_TAG=$(ROUTE_AGGREGATOR_IMAGE_TAG)"
	@echo "    done: optionally set route-aggregator image repository and tag independently"
	@echo "  TENANT=<tenant-name> (optional; example: TENANT=sample-env)"
	@echo "    done: include values.<tenant>.yaml overlays for models when needed"
	@echo "  AUTH_HELM_RELEASE=$(AUTH_HELM_RELEASE) PLATFORM_HELM_RELEASE=$(PLATFORM_HELM_RELEASE) MODELS_HELM_RELEASE=$(MODELS_HELM_RELEASE) PUBLISHER_HELM_RELEASE=$(PUBLISHER_HELM_RELEASE)"
	@echo "    done: override chart release names"
	@echo "  AUTH_HELM_CHART=$(AUTH_HELM_CHART) PLATFORM_HELM_CHART=$(PLATFORM_HELM_CHART) MODELS_HELM_CHART=$(MODELS_HELM_CHART) PUBLISHER_HELM_CHART=$(PUBLISHER_HELM_CHART)"
	@echo "    done: override chart locations"
	@echo "  TENANT=<tenant-name> (required for tenant-scoped model apply workflows; example: TENANT=sample-env)"
	@echo "    done: drives values.<tenant>.yaml for models chart used by apply-base/apply-model-reconciler flows"
	@echo "  HELM_EXTRA_ARGS='$(HELM_EXTRA_ARGS)'"
	@echo "    done: pass extra Helm flags to chart install/render commands"
	@echo "  FORCE=$(FORCE)"
	@echo "    done: when true, bypass target-cluster confirmation for mutating/apply targets"
	@echo "  PUBLISH_IF_MISSING=$(PUBLISH_IF_MISSING)"
	@echo "    done: when true, happy-publish skips pushing images that already exist in the registry"

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

# Validate TENANT and layered Helm values files.
.PHONY: require-tenant
require-tenant:
	@if [[ -z "$(TENANT)" ]]; then echo "Set TENANT=<tenant-name> (example: TENANT=sample-env)"; exit 1; fi

.PHONY: require-tenant-models
require-tenant-models: require-tenant
	@test -f "$(MODELS_HELM_CHART)/values.yaml" || (echo "Missing $(MODELS_HELM_CHART)/values.yaml" && exit 1)
	@test -f "$(MODELS_HELM_CHART)/values.$(TENANT).yaml" || (echo "Missing $(MODELS_HELM_CHART)/values.$(TENANT).yaml" && exit 1)

.PHONY: push-image-with-policy
push-image-with-policy: check-docker
	@if [[ -z "$(IMAGE)" ]]; then echo "Set IMAGE=<repository:tag>"; exit 1; fi
	@if [[ "$(PUBLISH_IF_MISSING)" =~ ^(1|true|yes|on)$$ ]]; then \
		if docker manifest inspect "$(IMAGE)" >/dev/null 2>&1; then \
			echo "Image already exists in registry, skipping push: $(IMAGE)"; \
		else \
			docker push "$(IMAGE)"; \
		fi; \
	else \
		docker push "$(IMAGE)"; \
	fi

.PHONY: confirm-target-cluster
confirm-target-cluster: check-kubectl
	@context=$$(kubectl config current-context 2>/dev/null || echo "<unknown>"); \
	cluster=$$(kubectl config view --minify -o 'jsonpath={.clusters[0].name}' 2>/dev/null || echo "<unknown>"); \
	server=$$(kubectl config view --minify -o 'jsonpath={.clusters[0].cluster.server}' 2>/dev/null || echo "<unknown>"); \
	user=$$(kubectl config view --minify -o 'jsonpath={.users[0].name}' 2>/dev/null || echo "<unknown>"); \
	default_namespace=$$(kubectl config view --minify -o 'jsonpath={..namespace}' 2>/dev/null || true); \
	if [[ -z "$$default_namespace" ]]; then default_namespace=default; fi; \
	echo "Target Kubernetes cluster:"; \
	echo "  context: $$context"; \
	echo "  cluster: $$cluster"; \
	echo "  server: $$server"; \
	echo "  user: $$user"; \
	echo "  kubeconfig namespace: $$default_namespace"; \
	echo "  target namespace: $(NAMESPACE)"; \
	echo "  auth namespace: $(AUTH_NAMESPACE)"; \
	if [[ "$(FORCE)" =~ ^(1|true|yes|on)$$ ]]; then \
		echo "FORCE=true, bypassing confirmation."; \
		exit 0; \
	fi; \
	read -r -p "Proceed against this cluster? [y/N] " confirm; \
	if [[ ! "$$confirm" =~ ^[Yy]$$ ]]; then \
		echo "Aborted."; \
		exit 1; \
	fi

# Step 1: Bootstrap base chart resources and namespace secrets.
.PHONY: render-helm-template
render-helm-template: check-helm
	helm template $(AUTH_HELM_RELEASE) $(AUTH_HELM_CHART) -n $(AUTH_NAMESPACE) $(AUTH_HELM_VALUES) --set-string authService.image=$(AUTH_IMAGE) $(HELM_EXTRA_ARGS)
	helm template $(PLATFORM_HELM_RELEASE) $(PLATFORM_HELM_CHART) -n $(NAMESPACE) $(PLATFORM_HELM_VALUES) --set authz.endpoint.namespace=$(AUTH_NAMESPACE) $(HELM_EXTRA_ARGS)
	helm template $(MODELS_HELM_RELEASE) $(MODELS_HELM_CHART) -n $(NAMESPACE) $(MODELS_HELM_VALUES) $(HELM_EXTRA_ARGS)
	helm template $(PUBLISHER_HELM_RELEASE) $(PUBLISHER_HELM_CHART) -n $(NAMESPACE) $(PUBLISHER_HELM_VALUES) $(HELM_EXTRA_ARGS)

.PHONY: helm-upgrade-base
helm-upgrade-base: confirm-target-cluster check-helm check-kubectl require-tenant-models
	helm upgrade --install $(AUTH_HELM_RELEASE) $(AUTH_HELM_CHART) -n $(AUTH_NAMESPACE) --create-namespace $(AUTH_HELM_VALUES) --set-string authService.image=$(AUTH_IMAGE) $(HELM_EXTRA_ARGS)
	helm upgrade --install $(PLATFORM_HELM_RELEASE) $(PLATFORM_HELM_CHART) -n $(NAMESPACE) --create-namespace $(PLATFORM_HELM_VALUES) --set authz.endpoint.namespace=$(AUTH_NAMESPACE) $(HELM_EXTRA_ARGS)
	helm upgrade --install $(MODELS_HELM_RELEASE) $(MODELS_HELM_CHART) -n $(NAMESPACE) --create-namespace $(MODELS_HELM_VALUES) --set modelReconciler.enabled=false $(HELM_EXTRA_ARGS)

.PHONY: apply-base

apply-base: confirm-target-cluster
	@$(MAKE) helm-upgrade-base FORCE=true TENANT="$(TENANT)" NAMESPACE="$(NAMESPACE)" AUTH_NAMESPACE="$(AUTH_NAMESPACE)" AUTH_IMAGE="$(AUTH_IMAGE)" HELM_EXTRA_ARGS="$(HELM_EXTRA_ARGS)"
	kubectl -n $(NAMESPACE) apply -f manifests/secrets.examples.yaml
	@if [[ "$(AUTH_NAMESPACE)" != "$(NAMESPACE)" ]]; then \
		kubectl -n $(AUTH_NAMESPACE) apply -f manifests/secrets.examples.yaml; \
	fi

# Step 2: Reconcile model catalog into running model workloads and services.
.PHONY: apply-serve-model
apply-serve-model: check-kubectl check-helm require-tenant-models
	@$(MAKE) apply-model-reconciler \
	  FORCE="$(FORCE)" \
	  TENANT="$(TENANT)" \
	  NAMESPACE="$(NAMESPACE)" \
	  CATALOG_KEY="$(CATALOG_KEY)" \
	  MODEL_SERVICE_TYPE="$(MODEL_SERVICE_TYPE)" \
	  MODEL_RECONCILER_IMAGE="$(MODEL_RECONCILER_IMAGE)" \
	  MODEL_RECONCILER_JOB_NAME="$(MODEL_RECONCILER_JOB_NAME)" \
	  MODEL_RECONCILER_WATCH_INTERVAL_SEC="0" \
	  HELM_EXTRA_ARGS="$(HELM_EXTRA_ARGS)"
	kubectl -n $(NAMESPACE) wait --for=condition=complete --timeout=20m job/$(MODEL_RECONCILER_JOB_NAME)

.PHONY: deploy-watch-local
deploy-watch-local: check-kubectl check-helm require-tenant-models
	@$(MAKE) apply-model-reconciler \
	  FORCE="$(FORCE)" \
	  TENANT="$(TENANT)" \
	  NAMESPACE="$(NAMESPACE)" \
	  CATALOG_KEY="$(CATALOG_KEY)" \
	  MODEL_SERVICE_TYPE="$(MODEL_SERVICE_TYPE)" \
	  MODEL_RECONCILER_IMAGE="$(MODEL_RECONCILER_IMAGE)" \
	  MODEL_RECONCILER_JOB_NAME="$(MODEL_RECONCILER_JOB_NAME)" \
	  MODEL_RECONCILER_WATCH_INTERVAL_SEC="$(MODEL_RECONCILER_WATCH_INTERVAL_SEC)" \
	  HELM_EXTRA_ARGS="$(HELM_EXTRA_ARGS)"

# Step 3: Generate test credentials and validate serving with smoke tests.
.PHONY: generate-auth-materials
generate-auth-materials:
	bash deploy/generate_test_auth_materials.sh --team-size 3 --jwt-sub dev-user --jwt-ttl-sec 3600

.PHONY: smoke-test-api-key
smoke-test-api-key: check-kubectl check-yq
	@if [[ -z "$(API_KEY)" ]]; then echo "Set API_KEY variable"; exit 1; fi
	bash deploy/smoke_test_lm_serve_catalog.sh \
	  --namespace $(NAMESPACE) \
	  --route-configmap $(ROUTE_CONFIGMAP) \
	  --route-key $(ROUTE_KEY) \
	  --api-key "$(API_KEY)" \
	  --prompt "$(PROMPT)"

.PHONY: smoke-test-jwt
smoke-test-jwt: check-kubectl check-yq
	@if [[ -z "$(JWT_TOKEN)" ]]; then echo "Set JWT_TOKEN variable"; exit 1; fi
	bash deploy/smoke_test_lm_serve_catalog.sh \
	  --namespace $(NAMESPACE) \
	  --route-configmap $(ROUTE_CONFIGMAP) \
	  --route-key $(ROUTE_KEY) \
	  --jwt-token "$(JWT_TOKEN)" \
	  --prompt "$(PROMPT)"


.PHONY: smoke-test-all
smoke-test-all: check-kubectl check-yq
	@if [[ -z "$(API_KEY)" ]]; then echo "Set API_KEY variable"; exit 1; fi
	@if [[ -z "$(JWT_TOKEN)" ]]; then echo "Set JWT_TOKEN variable"; exit 1; fi
	bash deploy/smoke_test_lm_serve_catalog.sh \
	  --namespace $(NAMESPACE) \
	  --route-configmap $(ROUTE_CONFIGMAP) \
	  --route-key $(ROUTE_KEY) \
	  --api-key "$(API_KEY)" \
	  --jwt-token "$(JWT_TOKEN)" \
	  --prompt "$(PROMPT)"

# Optional extension: Automate in-cluster model reconciliation.
.PHONY: build-auth-image
build-auth-image: check-docker
	docker build -t $(AUTH_IMAGE) auth-service

.PHONY: push-auth-image
push-auth-image: check-docker
	docker push $(AUTH_IMAGE)

.PHONY: apply-auth-service
apply-auth-service: confirm-target-cluster check-helm check-kubectl
	helm upgrade --install $(AUTH_HELM_RELEASE) $(AUTH_HELM_CHART) -n $(AUTH_NAMESPACE) --create-namespace $(AUTH_HELM_VALUES) --set-string authService.image=$(AUTH_IMAGE) $(HELM_EXTRA_ARGS)

.PHONY: build-route-aggregator-image
build-route-aggregator-image: check-docker
	docker build -t $(ROUTE_AGGREGATOR_IMAGE) route-aggregator

.PHONY: push-route-aggregator-image
push-route-aggregator-image: check-docker
	docker push $(ROUTE_AGGREGATOR_IMAGE)

.PHONY: apply-route-aggregator
apply-route-aggregator: confirm-target-cluster check-helm check-kubectl
	helm upgrade --install $(PLATFORM_HELM_RELEASE) $(PLATFORM_HELM_CHART) -n $(NAMESPACE) --create-namespace $(PLATFORM_HELM_VALUES) \
	  --set authz.endpoint.namespace=$(AUTH_NAMESPACE) \
	  --set routeConfig.liveUpdate.enabled=true \
	  --set-string routeConfig.liveUpdate.controller.image.repository=$(ROUTE_AGGREGATOR_IMAGE_REPOSITORY) \
	  --set-string routeConfig.liveUpdate.controller.image.tag=$(ROUTE_AGGREGATOR_IMAGE_TAG) \
	  $(HELM_EXTRA_ARGS)

# Optional extension: Automate in-cluster model reconciliation.
.PHONY: build-model-reconciler-image
build-model-reconciler-image: check-docker
	docker build -t $(MODEL_RECONCILER_IMAGE) deploy

.PHONY: push-model-reconciler-image
push-model-reconciler-image: check-docker
	docker push $(MODEL_RECONCILER_IMAGE)

.PHONY: apply-model-reconciler
apply-model-reconciler: confirm-target-cluster check-helm check-kubectl require-tenant-models
	kubectl -n $(NAMESPACE) delete job $(MODEL_RECONCILER_JOB_NAME) --ignore-not-found
	helm upgrade --install $(MODELS_HELM_RELEASE) $(MODELS_HELM_CHART) -n $(NAMESPACE) --create-namespace $(MODELS_HELM_VALUES) \
	  --set modelReconciler.enabled=true \
	  --set-string modelReconciler.name=$(MODEL_RECONCILER_JOB_NAME) \
	  --set-string modelReconciler.image=$(MODEL_RECONCILER_IMAGE) \
	  --set-string catalog.tenant=$(TENANT) \
	  --set-string modelReconciler.args.catalogKey=$(CATALOG_KEY) \
	  --set-string modelReconciler.args.modelServiceType=$(MODEL_SERVICE_TYPE) \
	  --set-string modelReconciler.args.watchIntervalSec=$(MODEL_RECONCILER_WATCH_INTERVAL_SEC) $(HELM_EXTRA_ARGS)

# Optional extension: Automate model publishing.
.PHONY: build-model-publisher-image
build-model-publisher-image: check-docker
	docker build -t $(PUBLISHER_IMAGE) publisher

.PHONY: push-model-publisher-image
push-model-publisher-image: check-docker
	docker push $(PUBLISHER_IMAGE)

.PHONY: apply-model-publisher
apply-model-publisher: confirm-target-cluster check-helm check-kubectl
	helm upgrade --install $(PUBLISHER_HELM_RELEASE) $(PUBLISHER_HELM_CHART) -n $(NAMESPACE) --create-namespace $(PUBLISHER_HELM_VALUES) \
	  --set publisher.enabled=true --set consumer.enabled=true \
	  --set-string publisher.image=$(PUBLISHER_IMAGE) $(HELM_EXTRA_ARGS)

# High-level orchestration targets.
.PHONY: happy-build
happy-build: check-docker check-helm require-tenant-models
	@echo "[1/5] Rendering all Helm charts for TENANT=$(TENANT)."
	@$(MAKE) render-helm-template TENANT="$(TENANT)" AUTH_NAMESPACE="$(AUTH_NAMESPACE)" HELM_EXTRA_ARGS="$(HELM_EXTRA_ARGS)"
	@echo "[2/5] Building auth-service image."
	@$(MAKE) build-auth-image AUTH_IMAGE="$(AUTH_IMAGE)"
	@echo "[3/5] Building model-reconciler image."
	@$(MAKE) build-model-reconciler-image MODEL_RECONCILER_IMAGE="$(MODEL_RECONCILER_IMAGE)"
	@echo "[4/5] Building route-aggregator image."
	@$(MAKE) build-route-aggregator-image ROUTE_AGGREGATOR_IMAGE="$(ROUTE_AGGREGATOR_IMAGE)"
	@echo "[5/5] Building model-publisher image."
	@$(MAKE) build-model-publisher-image PUBLISHER_IMAGE="$(PUBLISHER_IMAGE)"
	@echo "Happy build complete."

.PHONY: happy-publish
happy-publish: check-docker
	@echo "[1/4] Pushing auth-service image."
	@$(MAKE) push-image-with-policy IMAGE="$(AUTH_IMAGE)" PUBLISH_IF_MISSING="$(PUBLISH_IF_MISSING)"
	@echo "[2/4] Pushing model-reconciler image."
	@$(MAKE) push-image-with-policy IMAGE="$(MODEL_RECONCILER_IMAGE)" PUBLISH_IF_MISSING="$(PUBLISH_IF_MISSING)"
	@echo "[3/4] Pushing route-aggregator image."
	@$(MAKE) push-image-with-policy IMAGE="$(ROUTE_AGGREGATOR_IMAGE)" PUBLISH_IF_MISSING="$(PUBLISH_IF_MISSING)"
	@echo "[4/4] Pushing model-publisher image."
	@$(MAKE) push-image-with-policy IMAGE="$(PUBLISHER_IMAGE)" PUBLISH_IF_MISSING="$(PUBLISH_IF_MISSING)"
	@echo "Happy publish complete."

.PHONY: happy-apply
happy-apply: confirm-target-cluster
	@if [[ -z "$(API_KEY)" && -z "$(JWT_TOKEN)" ]]; then echo "Set API_KEY and/or JWT_TOKEN for smoke validation"; exit 1; fi
	@if [[ -z "$(TENANT)" ]]; then echo "Set TENANT=<tenant-name> (example: TENANT=sample-env)"; exit 1; fi
	@echo "[1/5] Installing auth, platform, and models baseline charts."
	@echo "      Why: creates foundational software and required namespace-scoped resources first."
	@$(MAKE) apply-base FORCE=true TENANT="$(TENANT)" AUTH_NAMESPACE="$(AUTH_NAMESPACE)" AUTH_IMAGE="$(AUTH_IMAGE)" HELM_EXTRA_ARGS="$(HELM_EXTRA_ARGS)"
	@echo "[2/5] Enabling platform live route update controller."
	@echo "      Why: route aggregation must be available before ongoing model route changes."
	@$(MAKE) apply-route-aggregator FORCE=true TENANT="$(TENANT)" AUTH_NAMESPACE="$(AUTH_NAMESPACE)" ROUTE_AGGREGATOR_IMAGE_REPOSITORY="$(ROUTE_AGGREGATOR_IMAGE_REPOSITORY)" ROUTE_AGGREGATOR_IMAGE_TAG="$(ROUTE_AGGREGATOR_IMAGE_TAG)" HELM_EXTRA_ARGS="$(HELM_EXTRA_ARGS)"
	@echo "[3/5] Enabling in-cluster model reconciler job."
	@echo "      Why: cluster-local reconciliation keeps model runtimes aligned with catalog changes."
	@$(MAKE) apply-model-reconciler FORCE=true TENANT="$(TENANT)" MODEL_RECONCILER_IMAGE="$(MODEL_RECONCILER_IMAGE)" HELM_EXTRA_ARGS="$(HELM_EXTRA_ARGS)"
	@echo "[4/5] Running one immediate reconciliation pass from local workstation."
	@echo "      Why: bootstraps model workloads immediately by running the in-cluster reconciler image in one-shot mode."
	@$(MAKE) apply-serve-model FORCE=true TENANT="$(TENANT)" NAMESPACE="$(NAMESPACE)" CATALOG_KEY="$(CATALOG_KEY)" MODEL_SERVICE_TYPE="$(MODEL_SERVICE_TYPE)" MODEL_RECONCILER_IMAGE="$(MODEL_RECONCILER_IMAGE)" MODEL_RECONCILER_JOB_NAME="$(MODEL_RECONCILER_JOB_NAME)" HELM_EXTRA_ARGS="$(HELM_EXTRA_ARGS)"
	@echo "[5/5] Running smoke test against enabled models through the edge endpoint."
	@echo "      Why: validates auth path plus basic inference availability after deployment."
	@if [[ -n "$(API_KEY)" && -n "$(JWT_TOKEN)" ]]; then \
		$(MAKE) smoke-test-all API_KEY="$(API_KEY)" JWT_TOKEN="$(JWT_TOKEN)" PROMPT="$(PROMPT)"; \
	elif [[ -n "$(API_KEY)" ]]; then \
		$(MAKE) smoke-test-api-key API_KEY="$(API_KEY)" PROMPT="$(PROMPT)"; \
	else \
		$(MAKE) smoke-test-jwt JWT_TOKEN="$(JWT_TOKEN)" PROMPT="$(PROMPT)"; \
	fi
	@echo "Happy apply complete."
	@echo "What next: use deploy-watch-local if you want continuous in-cluster reconciliation."

.PHONY: happy-release
happy-release: confirm-target-cluster
	@echo "[1/3] Building release artifacts."
	@$(MAKE) happy-build TENANT="$(TENANT)" AUTH_NAMESPACE="$(AUTH_NAMESPACE)" AUTH_IMAGE="$(AUTH_IMAGE)" MODEL_RECONCILER_IMAGE="$(MODEL_RECONCILER_IMAGE)" ROUTE_AGGREGATOR_IMAGE_REPOSITORY="$(ROUTE_AGGREGATOR_IMAGE_REPOSITORY)" ROUTE_AGGREGATOR_IMAGE_TAG="$(ROUTE_AGGREGATOR_IMAGE_TAG)" PUBLISHER_IMAGE="$(PUBLISHER_IMAGE)" HELM_EXTRA_ARGS="$(HELM_EXTRA_ARGS)"
	@echo "[2/3] Publishing container images."
	@$(MAKE) happy-publish AUTH_IMAGE="$(AUTH_IMAGE)" MODEL_RECONCILER_IMAGE="$(MODEL_RECONCILER_IMAGE)" ROUTE_AGGREGATOR_IMAGE="$(ROUTE_AGGREGATOR_IMAGE)" PUBLISHER_IMAGE="$(PUBLISHER_IMAGE)" PUBLISH_IF_MISSING="$(PUBLISH_IF_MISSING)"
	@echo "[3/3] Applying release to target cluster."
	@$(MAKE) happy-apply FORCE=true TENANT="$(TENANT)" API_KEY="$(API_KEY)" JWT_TOKEN="$(JWT_TOKEN)" PROMPT="$(PROMPT)" NAMESPACE="$(NAMESPACE)" AUTH_NAMESPACE="$(AUTH_NAMESPACE)" CATALOG_KEY="$(CATALOG_KEY)" MODEL_SERVICE_TYPE="$(MODEL_SERVICE_TYPE)" AUTH_IMAGE="$(AUTH_IMAGE)" MODEL_RECONCILER_IMAGE="$(MODEL_RECONCILER_IMAGE)" ROUTE_AGGREGATOR_IMAGE_REPOSITORY="$(ROUTE_AGGREGATOR_IMAGE_REPOSITORY)" ROUTE_AGGREGATOR_IMAGE_TAG="$(ROUTE_AGGREGATOR_IMAGE_TAG)" HELM_EXTRA_ARGS="$(HELM_EXTRA_ARGS)"
	@echo "Happy release complete."
