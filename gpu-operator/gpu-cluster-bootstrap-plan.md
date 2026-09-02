# GPU LKE Cluster Bootstrap Plan

**Cluster:** `pn-gpu-lke-in-bom-2` (Kubernetes 1.36, `in-bom-2`)  
**Nodes:** 2× `g2-gpu-rtx4000a1-m` — RTX 4000 Ada, ~20 GB VRAM each

---

## Overview

```
Phase 1: GPU Runtime Bootstrap       (cluster-level, one-time)
Phase 2: Supporting Infrastructure   (storage, ingress, observability)
Phase 3: Model Training              (fine-tuning HuggingFace models)
Phase 4: LLM Inference Serving       (vLLM or TGI)
```

---

## Phase 1 — GPU Runtime Bootstrap

### 1.1 NVIDIA GPU Operator

The single most important piece. Auto-installs and manages everything GPU-related on the nodes.

| Component | What it does |
|---|---|
| NVIDIA Driver | Kernel module on each node |
| NVIDIA Container Toolkit | containerd hook so containers can see GPUs |
| Device Plugin | Exposes `nvidia.com/gpu` as a schedulable k8s resource |
| DCGM Exporter | GPU metrics (temp, utilization, memory) |
| NFD (Node Feature Discovery) | Labels nodes with hardware capabilities |

```bash
helm repo add nvidia https://helm.ngc.nvidia.com/nvidia
helm repo update
helm install gpu-operator nvidia/gpu-operator \
  -n gpu-operator --create-namespace \
  --set driver.enabled=true \
  --set toolkit.enabled=true \
  --set devicePlugin.enabled=true \
  --set dcgm.enabled=true \
  --wait
```

> LKE nodes may already have drivers — GPU Operator handles this gracefully via its pre-install detection.

**Validation:**
```bash
kubectl get nodes -o custom-columns=NAME:.metadata.name,GPU:.status.allocatable."nvidia\.com/gpu"

kubectl run gpu-test --image=nvidia/cuda:12.4.0-base-ubuntu22.04 \
  --restart=Never --rm -it \
  --limits=nvidia.com/gpu=1 -- nvidia-smi
```

---

## Phase 2 — Supporting Infrastructure

### 2.1 Storage

LKE ships with the **Linode Block Storage CSI** pre-installed (ReadWriteOnce). Needed for:
- Dataset PVCs (training)
- Model weight PVCs (serving)

For **ReadWriteMany** (multiple pods sharing model weights), options:

| Option | Best for | How |
|---|---|---|
| NFS provisioner (in-cluster) | Cost-effective, smaller models | `nfs-subdir-external-provisioner` Helm chart backed by a Block Storage PVC |
| Linode Object Storage (S3-compatible) | Large model weights (70B+) | Download init container pulls from HuggingFace Hub to local PVC at pod startup |

For 7B–13B models (~14–28 GB), a 50–100 GB Block Storage PVC per node is sufficient.

```bash
# Optional: NFS provisioner for RWX
helm repo add nfs-subdir-external-provisioner \
  https://kubernetes-sigs.github.io/nfs-subdir-external-provisioner
helm install nfs-subdir-external-provisioner \
  nfs-subdir-external-provisioner/nfs-subdir-external-provisioner \
  -n nfs --create-namespace \
  --set nfs.server=<NFS_SERVER_IP> \
  --set nfs.path=/exported/path
```

### 2.2 Ingress + TLS

```bash
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm install ingress-nginx ingress-nginx/ingress-nginx \
  -n ingress-nginx --create-namespace

helm repo add jetstack https://charts.jetstack.io
helm install cert-manager jetstack/cert-manager \
  -n cert-manager --create-namespace \
  --set installCRDs=true
```

Creates a Linode NodeBalancer + Let's Encrypt TLS for the inference API endpoint.

### 2.3 Observability

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
  -n monitoring --create-namespace
```

GPU Operator's DCGM Exporter integrates automatically — GPU utilization, memory, and temperature dashboards appear in Grafana out of the box.

---

## Phase 3 — Model Training

### Hardware Capability Matrix

| Model Size | Precision | Strategy |
|---|---|---|
| ≤ 3B | fp16/bf16 | Single GPU, full fine-tune |
| 7B (Llama-3.1-8B, Mistral-7B) | bf16 | Single GPU (fits in 16 GB), or 2-GPU DDP |
| 7B | QLoRA (4-bit) | Single GPU, very memory efficient |
| 13B | QLoRA | Single GPU |
| 13B | bf16 | 2-GPU with FSDP/DeepSpeed |
| 70B | QLoRA | Does **not** fit on 2×20 GB — needs 4+ A100s |

### Recommended Training Stack (HuggingFace ecosystem)

| Library | Purpose |
|---|---|
| `transformers` | Model loading, Trainer loop |
| `peft` | LoRA / QLoRA adapters |
| `trl` | SFTTrainer, DPOTrainer, GRPOTrainer |
| `accelerate` | Multi-GPU orchestration (DDP / FSDP) |
| `bitsandbytes` | 4-bit/8-bit quantization for QLoRA |
| `datasets` | Dataset loading from Hub or local PVC |

**Base container image:** `nvcr.io/nvidia/pytorch:24.05-py3` (NGC, includes CUDA 12.4 + PyTorch)

### HuggingFace Token Secret

```bash
kubectl create secret generic hf-credentials \
  --from-literal=token=hf_xxxxxxxxxxxx \
  -n default
```

### Training Job (k8s Job pattern)

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: finetune-mistral-7b
spec:
  template:
    spec:
      nodeSelector:
        linode-plan: g2-gpu-rtx4000a1-m
      tolerations:
        - key: linode-plan
          operator: Exists
      containers:
        - name: trainer
          image: nvcr.io/nvidia/pytorch:24.05-py3
          resources:
            limits:
              nvidia.com/gpu: "1"
          env:
            - name: HF_TOKEN
              valueFrom:
                secretKeyRef:
                  name: hf-credentials
                  key: token
          volumeMounts:
            - name: model-storage
              mountPath: /models
      volumes:
        - name: model-storage
          persistentVolumeClaim:
            claimName: model-pvc
      restartPolicy: Never
```

### Distributed Training (2 nodes)

Use **Kubeflow Training Operator** for `PyTorchJob` CRD (DDP across both GPU nodes via NCCL):

```bash
kubectl apply -k \
  "github.com/kubeflow/training-operator/manifests/overlays/standalone?ref=v1.8.0"
```

---

## Phase 4 — LLM Inference Serving

### Option A: vLLM (recommended)

Best performance, PagedAttention KV-cache management, OpenAI-compatible API (`/v1/chat/completions`).

```bash
helm repo add vllm https://vllm-project.github.io/production-stack
helm install vllm vllm/vllm-stack \
  -n vllm --create-namespace \
  --set model=mistralai/Mistral-7B-Instruct-v0.3 \
  --set gpu_memory_utilization=0.9 \
  --set resources.limits."nvidia\.com/gpu"=1
```

For tensor parallelism across both GPUs (e.g. 70B 4-bit):
```bash
--set tensor_parallel_size=2
```

### Option B: HuggingFace TGI (Text Generation Inference)

Simpler for tight HuggingFace Hub integration:

```bash
# Deployment using ghcr.io/huggingface/text-generation-inference:latest
# --model-id meta-llama/Llama-3.1-8B-Instruct
# --num-shard 1   (or 2 for tensor parallelism across both GPUs)
```

### Model Sizing for Serving (RTX 4000 Ada, 20 GB VRAM)

| Model | Precision | VRAM needed | Fits? |
|---|---|---|---|
| Mistral-7B / Llama-3.1-8B | fp16 | ~16 GB | ✅ 1 GPU |
| Llama-3.1-8B | 4-bit GPTQ/AWQ | ~5 GB | ✅ 1 GPU |
| Llama-3.1-13B | 4-bit | ~8 GB | ✅ 1 GPU |
| Llama-3.1-70B | 4-bit | ~40 GB | ✅ 2 GPUs (tensor parallel) |
| Llama-3.1-70B | fp16 | ~140 GB | ❌ |

---

## Complete Component Summary

| Layer | Tool | Install method |
|---|---|---|
| GPU runtime | NVIDIA GPU Operator | Helm |
| Storage (RWO) | Linode CSI (built-in) | pre-installed |
| Storage (RWX) | nfs-subdir-external-provisioner | Helm |
| Model registry | HuggingFace Hub (HF_TOKEN secret) | `kubectl create secret` |
| Distributed training | Kubeflow Training Operator | kustomize |
| Inference serving | vLLM or HF TGI | Helm / Deployment |
| Ingress | ingress-nginx | Helm |
| TLS | cert-manager | Helm |
| Monitoring | kube-prometheus-stack + DCGM | Helm |

---

## Suggested Execution Order

1. Deploy GPU Operator → validate `nvidia.com/gpu` appears in node allocatable
2. Set up storage → PVC for models and dataset
3. Create HuggingFace token secret
4. Run a training Job → small QLoRA fine-tune to validate pipeline end-to-end
5. Deploy vLLM with a base model (e.g. Mistral-7B-Instruct) → test inference API
6. Swap in fine-tuned adapter → merge LoRA weights and upload to Hub, or load via PEFT plugin in vLLM
7. Expose via Ingress + TLS for external access

---

## Key Constraint

Each `g2-gpu-rtx4000a1-m` node has **1× RTX 4000 Ada (20 GB VRAM)**. Sweet spot:
- **Training:** QLoRA on 7B–13B models
- **Serving:** 7B models in fp16, or up to 70B with 4-bit quantization + tensor parallelism across both nodes
