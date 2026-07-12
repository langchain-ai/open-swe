---
name: langchainplus-gateway-auth
description: Answer questions about LLM Gateway access, deployment-key permissions, gateway:invoke, and "does the default deployment key have gateway access" in the langchain-ai/langchainplus repo. Use this captured architecture map first and only grep/read to confirm specifics or detect drift, instead of re-deriving the whole auth path with a full grep/read fan-out each session.
---

# langchainplus LLM-Gateway / deployment-key auth

This is the settled architecture map for how deployment keys are minted and how the
LLM Gateway authorizes them in `langchain-ai/langchainplus`. It exists so this class of
question can be answered from written knowledge instead of re-running the same ~35 grep /
~30 read_file cold-start investigation across the same ~14 paths every session.

**Consult this map first.** Only `grep`/`read_file` to confirm a specific line the answer
turns on, or to detect drift if the files below have changed since this was written. Do
not re-derive the whole map from scratch.

## Bottom line (the settled answer)

The default deployment key issued by host-backend is **not** an org-admin / gateway-admin
key, and it does **not** carry a per-key `gateway:invoke` grant. Whether a runtime call can
invoke the LLM Gateway depends on **org-level LLM-Gateway enablement plus env-var / key
wiring** — not on a permission bit stamped onto the deployment key itself. So "does the
default key have gateway access?" is an env-var/key-wiring and org-enablement question, not
a missing-scope-on-the-key question.

## 1. Deployment-key minting and scope

Where keys are created and what scope they carry:

- **host-backend (split-plane API keys)**
  - `host/models/env_var.py` — env-var / key material wiring for the deployment.
  - `host/models/host_metadata_crud.py` — host-side metadata + split-plane key CRUD.
- **smith-backend (`api_keys`)**
  - `app/models/api_keys/crud.py` — api_key create/read/update logic and scope assignment.
  - `app/models/api_keys/proxy.py` — proxy/service-key handling.
  - `app/api/endpoints/api_keys.py` — the api_keys HTTP endpoints.

The default deployment key is a split-plane key minted here with an ordinary
(non-org-admin) scope; it is not stamped with gateway-admin privileges.

## 2. Gateway authorization enforcement

Where an incoming key is authorized against the gateway (smith-go):

- `smith-go/auth/api_key.go` — api-key authentication.
- `smith-go/auth/auth.go` — auth entrypoint / principal resolution.
- `smith-go/gateway_policies/handler.go` — evaluates gateway guard policies for a request.
- `smith-go/gateway/middleware.go` — gateway request middleware that gates invoke.
- `smith-go/authz_internal/constants.go` — authz constant/permission definitions.

Runtime invoke is gated here by policy + org enablement, not by a `gateway:invoke` bit read
off the deployment key.

## 3. The gateway guard policy model

- `smith-backend/alembic/versions/2026_05_01_1200-725370a17755_gateway_guard_policies.py`
  — the `gateway_guard_policies` migration that defines the policy model the smith-go
  `gateway_policies/handler.go` enforces.

## Answering the question

Combine the above: the default deployment key is a normal split-plane / api_keys key (§1),
gateway invoke is enforced by smith-go policy + middleware (§2) against the
`gateway_guard_policies` model (§3), and there is no per-key `gateway:invoke` grant. So the
answer to "does the default deployment key carry gateway:invoke?" is: no — gateway access
comes from org LLM-Gateway enablement plus env-var/key wiring, not from a scope on the key.

Only open the files above if you need to quote an exact line or check whether the code has
drifted from this map.
