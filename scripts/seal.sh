#!/usr/bin/env sh
# Seal a Secret for the aws overlays, offline, against the committed sealing certificate.
#
# Runs inside the ops container (alpine/k8s: kubectl + kubeseal). No cluster access is
# needed or used: the certificate at platform/sealed-secrets/sealing-cert.crt is the public
# half of the controller's key, and anything sealed with it can be decrypted only by a
# cluster holding the private half -- which `mise run platform-apply` restores from
# certs/sealed-secrets-key.yaml before the controller starts.
#
#   scripts/seal.sh <namespace> <name> <out-file> key=value [key=value ...]
#   mise run seal -- monitoring alertmanager-discord monitoring/overlays/aws/sealed/alertmanager-discord.yaml webhook-url=https://discord.com/api/webhooks/...
#
# The value never touches git, a shell history file only if the operator's shell keeps
# one, and stdout not at all: the only thing written is the sealed file. Strict scope
# (the default): the SealedSecret decrypts only into this namespace and name, so a
# committed file cannot be renamed into a different Secret by editing its metadata.

set -eu

if [ "$#" -lt 4 ]; then
  echo "usage: $0 <namespace> <name> <out-file> key=value [key=value ...]" >&2
  exit 2
fi

ns="$1"; name="$2"; out="$3"; shift 3
cert="platform/sealed-secrets/sealing-cert.crt"

if [ ! -s "$cert" ]; then
  echo "error: $cert is missing or empty; run: mise run seal-export (against a cluster whose key you keep)" >&2
  exit 1
fi

literals=""
for kv in "$@"; do
  case "$kv" in
    *=*) literals="$literals --from-literal=$kv" ;;
    *) echo "error: '$kv' is not key=value" >&2; exit 2 ;;
  esac
done

mkdir -p "$(dirname "$out")"

# shellcheck disable=SC2086  # $literals is deliberately word-split into repeated flags
kubectl create secret generic "$name" --namespace "$ns" $literals --dry-run=client -o yaml \
  | kubeseal --cert "$cert" --format yaml > "$out"

echo "sealed $ns/$name -> $out (keys: $(printf '%s\n' "$@" | cut -d= -f1 | paste -sd, -))"
