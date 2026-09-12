#!/usr/bin/env sh
# Static checks for every Terraform environment: formatting, then a full validate.
#
# Runs `init -backend=false` because validate needs the providers and modules resolved but
# must not touch remote state — that is what makes this safe to run in CI on a pull
# request from a fork, with no credentials present at all.
#
# POSIX sh, not bash: this runs inside the hashicorp/terraform image, which ships busybox.
#
# Usage:  scripts/tf-check.sh            # from the repo root, inside the terraform image
#         make tf-check                  # the way it is actually invoked

set -eu

TF_DIR="infra/terraform"
ENVS="bootstrap aws aws-burst local"
failed=""

echo "==> terraform fmt"
if ! terraform fmt -recursive -check -diff "$TF_DIR"; then
  echo "    formatting differences above; run: make tf-fmt" >&2
  failed="fmt"
fi

for env in $ENVS; do
  echo ""
  echo "==> $env: init"
  # -input=false so a missing value fails instead of hanging a CI job on a prompt.
  if ! terraform -chdir="$TF_DIR/envs/$env" init -backend=false -input=false -no-color; then
    failed="$failed init:$env"
    continue
  fi

  echo "==> $env: validate"
  if ! terraform -chdir="$TF_DIR/envs/$env" validate -no-color; then
    failed="$failed validate:$env"
  fi
done

echo ""
if [ -n "$failed" ]; then
  echo "FAILED:$failed" >&2
  exit 1
fi
echo "All environments formatted and valid."
