#!/usr/bin/env sh
# Prove the Sealed Secrets round trip on the live cluster, end to end.
#
# Runs inside the ops container. Fetches the controller's certificate through the API
# server, seals a fixture with it, applies the SealedSecret, and asserts that the plain
# Secret the controller produces holds the original value -- then deletes both. Every
# step is the one the aws overlays depend on; a controller that is Running but cannot
# decrypt (wrong key, RBAC, a webhook in the way) fails here and nowhere in `kubectl get
# pods`.
#
# Also reports whether the COMMITTED certificate is the live cluster's. It need not be:
# on a machine without certs/sealed-secrets-key.yaml, a rebuilt cluster has a fresh key
# and the committed certificate belongs to someone else's. That is not a failure of the
# mechanism, so it is printed rather than asserted -- but it does mean nothing sealed in
# the aws overlays can be decrypted by this cluster until `mise run seal-export` has run.
#
#   mise run seal-check

set -eu

ns="default"
name="seal-check"
ctrl_ns="sealed-secrets"
ctrl="sealed-secrets-controller"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"; kubectl delete sealedsecret "$name" -n "$ns" --ignore-not-found >/dev/null 2>&1 || true' EXIT

echo "==> controller"
kubectl rollout status "deploy/$ctrl" -n "$ctrl_ns" --timeout=120s

echo "==> live certificate"
kubeseal --controller-namespace "$ctrl_ns" --controller-name "$ctrl" --fetch-cert > "$tmp/live.crt"
test -s "$tmp/live.crt"

if [ -s platform/sealed-secrets/sealing-cert.crt ]; then
  if cmp -s "$tmp/live.crt" platform/sealed-secrets/sealing-cert.crt; then
    echo "    committed sealing-cert.crt matches the live cluster"
  else
    echo "    note: committed sealing-cert.crt is NOT this cluster's key (fine locally; run seal-export before sealing anything for it)"
  fi
else
  echo "    note: no committed sealing-cert.crt yet; run: mise run seal-export"
fi

echo "==> seal a fixture and apply it"
kubectl create secret generic "$name" -n "$ns" --from-literal=value=hello --dry-run=client -o yaml \
  | kubeseal --cert "$tmp/live.crt" --format yaml > "$tmp/sealed.yaml"
grep -q "^kind: SealedSecret" "$tmp/sealed.yaml"
# The sealed file must not contain the plaintext. Cheap, and the one check that catches
# a kubeseal invocation that silently fell back to writing the Secret through.
if grep -q "aGVsbG8=" "$tmp/sealed.yaml" || grep -q "value: hello" "$tmp/sealed.yaml"; then
  echo "FAIL: plaintext present in sealed output" >&2
  exit 1
fi
kubectl apply -f "$tmp/sealed.yaml" >/dev/null

echo "==> the controller unseals it"
kubectl wait --for=create "secret/$name" -n "$ns" --timeout=60s >/dev/null
# Asserting on the decrypted bytes rather than on the Secret's existence: a controller
# with the wrong key produces no Secret at all, and one with a bug could in principle
# produce the wrong one. (Not `kubectl wait --for=jsonpath=...=aGVsbG8=`: the expected
# value's base64 padding is an `=`, which that flag's parser splits on.)
got="$(kubectl get secret "$name" -n "$ns" -o go-template='{{index .data "value" | base64decode}}')"
if [ "$got" != "hello" ]; then
  echo "FAIL: secret/$name data.value decrypted to '$got', wanted 'hello'" >&2
  exit 1
fi
echo "    secret/$name data.value decrypts to 'hello'"

echo "==> owner reference"
# Deleting the SealedSecret must delete the Secret: the controller sets an ownerReference,
# and if it did not, a secret removed from git would live on in the cluster.
kubectl delete sealedsecret "$name" -n "$ns" >/dev/null
i=0
while kubectl get secret "$name" -n "$ns" >/dev/null 2>&1; do
  i=$((i+1)); [ "$i" -gt 30 ] && { echo "FAIL: secret/$name survived deletion of its SealedSecret" >&2; exit 1; }
  sleep 1
done
echo "    secret/$name garbage-collected with its SealedSecret"

echo ""
echo "seal-check: PASS"
