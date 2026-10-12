#!/usr/bin/env bash
# A VictoriaLogs store of made-up gateway traffic, for trying replay datasets
# (Automatic Tuning > Datasets) without a gateway: deployed into the cluster
# the platforms run on, and filled with a day of chat requests for two models
# on three backends, in the shape the gateway's request log has there.
#
#   deploy/dev/demo-traffic.sh            deploy it and load the traffic
#   deploy/dev/demo-traffic.sh down       delete it
#
# Then install, or run the install again, with its address, which turns on
# LLMBench's dataset collector and gives the dataset form its default:
#   VICTORIALOGS_URL=http://victorialogs.victorialogs:9428 deploy/quickstart.sh
#
# The requests are random text: replaying them measures nothing.
#
# Options:
#   --context NAME   the platforms' kubectl context (default: the current one)
#   --requests N     how many requests to make up (default: 4000)
set -euo pipefail

say() { printf '\n==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
NS=victorialogs
IMAGE=victoriametrics/victoria-logs:v1.51.1

action=up ctx="" requests=4000
while [ $# -gt 0 ]; do
  case "$1" in
    down) action=down ;;
    --context) [ $# -ge 2 ] || die "--context needs a value"; ctx=$2; shift ;;
    --requests) [ $# -ge 2 ] || die "--requests needs a value"; requests=$2; shift ;;
    -h|--help) sed -n '2,/^set -euo/p' "$0" | sed '$d'; exit 0 ;;
    *) die "unknown argument: $1 (see --help)" ;;
  esac
  shift
done
case "$requests" in ''|*[!0-9]*) die "--requests must be a number" ;; esac
for tool in kubectl python3 curl; do
  command -v "$tool" >/dev/null 2>&1 || die "$tool is required"
done
[ -n "$ctx" ] || ctx=$(kubectl config current-context)
k() { kubectl --context "$ctx" "$@"; }

if [ "$action" = down ]; then
  k delete namespace "$NS" --ignore-not-found
  exit 0
fi

say "Deploying VictoriaLogs into namespace $NS (context $ctx)"
k apply -f - >/dev/null <<EOF
apiVersion: v1
kind: Namespace
metadata:
  name: $NS
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: victorialogs
  namespace: $NS
spec:
  replicas: 1
  selector:
    matchLabels: {app: victorialogs}
  template:
    metadata:
      labels: {app: victorialogs}
    spec:
      containers:
        - name: victorialogs
          image: $IMAGE
          args: ["-storageDataPath=/data", "-retentionPeriod=14d"]
          ports:
            - containerPort: 9428
          readinessProbe:
            httpGet: {path: /health, port: 9428}
          volumeMounts:
            - {name: data, mountPath: /data}
      volumes:
        - name: data
          emptyDir: {}
---
apiVersion: v1
kind: Service
metadata:
  name: victorialogs
  namespace: $NS
spec:
  selector: {app: victorialogs}
  ports:
    - port: 9428
      targetPort: 9428
EOF
k -n "$NS" rollout status deploy/victorialogs --timeout=300s >/dev/null ||
  die "VictoriaLogs did not start; is $IMAGE reachable from the cluster? (kubectl -n $NS describe pod)"

say "Loading $requests made-up requests"
port=19428
k -n "$NS" port-forward svc/victorialogs "$port:9428" >/dev/null 2>&1 &
forward=$!
trap 'kill "$forward" 2>/dev/null || true' EXIT
for _ in $(seq 30); do
  curl -fs "http://127.0.0.1:$port/health" >/dev/null 2>&1 && break
  sleep 1
done
python3 "$REPO/deploy/dev/demo_traffic.py" --url "http://127.0.0.1:$port" --requests "$requests"

cat <<EOF

Done. In the cluster it is http://victorialogs.$NS:9428. Install with it:
  VICTORIALOGS_URL=http://victorialogs.$NS:9428 deploy/quickstart.sh
then open Automatic Tuning > Datasets and press New dataset. The traffic has
models qwen3-8b (backends 192.0.2.11 and 192.0.2.12) and llama-3.1-8b
(192.0.2.13). Running this again adds another day of requests.
EOF
