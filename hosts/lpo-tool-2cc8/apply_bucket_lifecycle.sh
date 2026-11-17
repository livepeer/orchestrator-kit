#!/usr/bin/env bash
# Set (or, with --check, verify) the delete rule of lpo-tool-2cc8's output bucket from lifecycle.json.
#
# The bucket is not created by Pulumi and the VM's identity cannot administer it, so an operator with
# storage.buckets.update on project naap-orchestrator runs this from a workstation. The rule used to be set
# by hand at 2 days, which deleted users' results before the 7-day storage window the Beta Terms promise
# (LPA-527). It now matches the fleet's own tool-output bucket: 30 days.
#
#   ./apply_bucket_lifecycle.sh           # apply lifecycle.json, then print the live rule
#   ./apply_bucket_lifecycle.sh --check   # exit 1 if the live rule differs from lifecycle.json
set -euo pipefail

bucket="gs://lpo-tool-2cc8-outputs"
here="$(cd "$(dirname "$0")" && pwd)"
want="$(python3 -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1]))["rule"], sort_keys=True))' "$here/lifecycle.json")"

live_rule() {
  gcloud storage buckets describe "$bucket" --format=json \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps((d.get("lifecycle_config") or {}).get("rule", []), sort_keys=True))'
}

if [ "${1:-}" = "--check" ]; then
  got="$(live_rule)"
  if [ "$got" != "$want" ]; then
    echo "drift: live rule $got, lifecycle.json $want" >&2
    exit 1
  fi
  echo "ok: $bucket rule matches lifecycle.json"
  exit 0
fi

gcloud storage buckets update "$bucket" --lifecycle-file="$here/lifecycle.json"
got="$(live_rule)"
echo "live rule: $got"
[ "$got" = "$want" ]
