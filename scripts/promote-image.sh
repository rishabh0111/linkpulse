#!/bin/sh
# Pin the aws overlay to one image, by digest.
#
# Run by CI's `promote` job once the image is pushed and e2e has passed, and runnable by
# hand in the same container:
#
#   docker compose run --rm --entrypoint sh ops scripts/promote-image.sh \
#     ghcr.io/<owner>/linkpulse sha256:<64 hex>
#
# Not `kustomize edit set image`, which rewrites the whole file and drops every comment
# in it, and not `yq -i`, which keeps the comments but drops every blank line. In that
# overlay the comments are the argument for each line; a promotion should be a two-line
# diff.
#
# Leaves the change in the working tree. Committing it is the caller's job, so this
# script can be run and inspected locally without touching git.
set -eu

if [ $# -ne 2 ]; then
  echo "usage: $0 <image, no tag> <sha256:digest>" >&2
  exit 2
fi
image=$1
digest=$2
overlay=k8s/manifests/overlays/aws
placeholder=ghcr.io/owner/linkpulse

# Both arrive from another job's outputs. An empty digest -- a build that did not push --
# would otherwise be written as `digest: ""` and render an image kustomize accepts and
# no node can pull.
printf '%s' "$digest" | grep -Eq '^sha256:[0-9a-f]{64}$' || {
  echo "not a sha256 digest: '$digest'" >&2
  exit 2
}
# ghcr.io paths must be lowercase, and a tag or digest here would be doubled up with the
# one written below.
printf '%s' "$image" | grep -Eq '^[a-z0-9]+([._/-][a-z0-9]+)*$' || {
  echo "not a lowercase image name without tag or digest: '$image'" >&2
  exit 2
}

# Rewrites the one images entry and nothing else: every newName/newTag/digest line under
# `- name: <placeholder>` is dropped and the two pinned lines take their place, so a
# second promotion replaces the first rather than stacking under it.
file=$overlay/kustomization.yaml
awk -v name="$placeholder" -v image="$image" -v digest="$digest" '
  in_entry && /^    (newName|newTag|digest):/ { next }
  in_entry && !/^    / { in_entry = 0 }
  { print }
  $0 == "  - name: " name {
    in_entry = 1
    print "    newName: " image
    print "    digest: " digest
  }
' "$file" > "$file.tmp"
mv "$file.tmp" "$file"

# Assert on the rendered Deployment, not on the file: a renamed placeholder in the base
# or a reindented entry would leave the edit above matching nothing, silently.
rendered=$(kubectl kustomize "$overlay" | yq 'select(.kind == "Deployment") | .spec.template.spec.containers[].image')
if [ "$rendered" != "$image@$digest" ]; then
  echo "rendered image is '$rendered', expected '$image@$digest'" >&2
  exit 1
fi
echo "aws overlay now renders $rendered"
