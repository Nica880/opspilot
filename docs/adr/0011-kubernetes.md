# ADR-011: Kubernetes mode: k3d, own Helm charts, Argo CD app-of-apps, KEDA on consumer lag

- Status: accepted
- Date: 2026-11-26

## Context

The project should show a production-style deployment (GitOps, autoscaling) that still runs for
free on the Mac, from the same images as `make up`, outside the 30-minute demo budget.

## Decision

- **k3d** (k3s v1.35.10 in Docker on Colima), single server node. Traefik is disabled. The UI,
  API, Phoenix and Temporal UI are NodePorts mapped to the same host ports as compose (3000, 8000,
  6006, 8233). The web image has `NEXT_PUBLIC_API_URL=http://localhost:8000` baked in, so the
  **same images** work in both modes. `make k8s-up` builds them once and `k3d image import`s
  them; no registry is needed.
- **Two Helm charts we own:**
  - `opspilot-infra`: Postgres, Qdrant, Redpanda (+ topic Job), Temporal + UI, Phoenix, with the
    same pinned arm64 images as compose. Bitnami charts moved behind a paywall in 2025, and the
    upstream operators are heavy for one laptop.
  - `opspilot`: one Deployment per Python component, rendered from a single `python:` map in
    values (api, worker, indexer, triage-trigger); web; a migration Job (Argo CD `PreSync`, Helm
    pre-install/upgrade hook); KEDA ScaledObjects. Pods run as non-root with all capabilities
    dropped.
- **Argo CD app-of-apps** (`deploy/argocd/root.yaml` → `deploy/argocd/apps/`) from this GitHub
  repo. Sync waves order the children: KEDA (−2, official chart 2.21.1, server-side apply for its
  large CRDs), infra (−1), app (0, with retry/backoff while infra starts). Argo CD v3.6.3 is
  installed from its pinned upstream manifest.
- **KEDA scales on Redpanda consumer-group lag** (`kafka` scaler):
  - `indexer`: on `opspilot-indexer` lag (threshold 20);
  - `worker`: on `opspilot-triage` lag (threshold 5); waiting triage events mean LLM work is
    coming.
  - Both scale from 1 to 3; at most 6, the partition count.
- **Secrets** never go to git: `make k8s-up` turns `.env` into the `opspilot-secrets` Secret,
  which the pods reference as `optional`.
- **Ollama stays on the host** (Metal); pods reach it at `host.k3d.internal:11434`.
- **CI**: `make k8s-lint` runs `helm lint --strict` on both charts, then `kubeconform -strict` on
  the rendered manifests and the Argo CD Applications. CRD schemas for `ScaledObject` and
  `Application` come from the datreeio CRDs catalog, so they are validated, not skipped.

## Consequences

- No cluster runs in CI; the k8s path is validated statically. `make k8s-up` is the end-to-end
  check on the Mac.
- Single-node storage (local-path, RWO) is shared by the pods for the model cache; a multi-node
  cluster would need RWX storage or per-pod caches.
