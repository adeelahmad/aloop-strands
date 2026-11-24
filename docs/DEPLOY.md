# Deploying aloop-strands

aloop-strands needs two things on the host: Python 3.11+ and the **Strands Box** CLI built from the
pinned revision `8c26868a952e954ba1b99b7d7bf46b67da94f4c5` (`aloop_strands.box.BOX_REV`). The released
Box v0.3.0 lacks `box run --config`; aloop-strands refuses it (`BoxError`) rather than run unsandboxed.
Without Box, `run` refuses unless you pass `--no-box` (development only; gates still apply in-process).

## Configuration (env only; no secrets in the image)

| Variable | Meaning | Default |
|---|---|---|
| `ALOOP_MODEL` | Model for every role, `provider/name` | `bedrock/global.anthropic.claude-sonnet-4-6` |
| `ALOOP_MODEL_PLANNING`, `ALOOP_MODEL_WORKER` | Per-tier overrides | `ALOOP_MODEL` |
| `ALOOP_MODEL_HOST` | The only egress host workers may reach (unset: no egress) | none |
| `ALOOP_MODEL_SECRET_ENV` | Name of the env var Box injects as the credential on that host | none |
| `ALOOP_ATTEMPT_TOKENS`, `ALOOP_RUN_TOKENS` | Hard token budgets (gates) | 400000 / 5000000 |
| `ALOOP_BOX_BIN` | Box binary | `box` on PATH |
| `ALOOP_BOX_STATE` | Box state dir (short path, 0700) | `/tmp/aloop-box` |
| `AWS_REGION` + AWS creds / `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | Model credential | none |

Credentials are injected by Box as `secret.ref env://NAME` on the model egress rule: the worker
process never sees the key; only Box's proxy adds it to requests to `ALOOP_MODEL_HOST`.

## Local: Docker Compose

```sh
docker compose build
docker compose run --rm cli init --root /work
docker compose run --rm cli plan "a semver parser" --root /work
docker compose up dashboard            # http://127.0.0.1:8787 : review plan, approve with the code
docker compose run --rm cli run --root /work --wait
```

The container needs user namespaces for Box: compose sets `seccomp`, `apparmor` and `systempaths`
to `unconfined`. On Ubuntu 24.04 hosts also run
`sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0`.

## Linux host (no container)

```sh
cargo install --git https://github.com/strands-agents/box --rev 8c26868a952e954ba1b99b7d7bf46b67da94f4c5 strands-box strands-box-containment
pip install 'aloop-strands[dashboard]'      # or: pip install -e . from a checkout
aloop-strands init && aloop-strands run "a semver parser"
```

## EKS

Follow Box's Kubernetes guidance: one Kubernetes Job per task, pod spec with
`hostUsers: false`, `securityContext.procMount: Unmasked`, `seccompProfile: Unconfined`,
and the image from this repo's Dockerfile. Keep the dashboard as a Deployment bound to the cluster
network only (Service type ClusterIP, reach it with `kubectl port-forward`). Model credentials come
from IRSA (Bedrock) or a Kubernetes Secret mapped to env; never bake them into the image. The project
repo lives on a PVC or is cloned per Job; the event log (`.aladdin/logs/events.jsonl`) must sit on the
PVC so the hash chain survives pods.

## Amazon Bedrock AgentCore Runtime

Package the same image; set the container entrypoint to `aloop-strands run --root /work --wait`.
AgentCore supplies the Bedrock credential through its execution role, so set only `ALOOP_MODEL`
and `AWS_REGION`. AgentCore's microVM is the outer sandbox; Box still runs inside it for the
Dogwood policy (commit-after-tests, gate files hidden, egress allow-list).
