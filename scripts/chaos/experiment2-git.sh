#!/usr/bin/env sh
# The git side of chaos experiment 2: push a broken deploy, then revert it.
#
# Runs inside the ops container (git, no host git needed) against the Gitea remote ArgoCD
# watches -- never against the repository the developer is sitting in. The break is one
# line: the local overlay's image tag goes from `local` to `bad`, the image built by
# `mise run app-build-bad` (readiness fails, liveness passes). The fix is `git revert`,
# because the property under test is "rollback is a reverted commit, never a manual
# patch" -- selfHeal would undo a kubectl rollback within the minute anyway.
#
#   scripts/chaos/experiment2-git.sh break     # commit + push newTag: bad
#   scripts/chaos/experiment2-git.sh revert    # git revert HEAD + push
#
# Writes the pushed commit's short hash to docs/evidence/chaos-2/<action>.rev (relative
# to the repository this is run from), which experiment2.py reads to know the revision
# ArgoCD must reach -- a file rather than stdout, so no shell has to capture it.

set -eu

remote="${CHAOS_GIT_REMOTE:-http://linkpulse:linkpulse@gitea:3000/linkpulse/linkpulse.git}"
overlay="k8s/manifests/overlays/local/kustomization.yaml"
work="/tmp/chaos-2-clone"
action="${1:?usage: $0 break|revert}"
evidence="$(pwd)/docs/evidence/chaos-2"
mkdir -p "$evidence"

if [ ! -d "$work/.git" ]; then
  git clone -q "$remote" "$work"
fi
cd "$work"
git config user.email "chaos@linkpulse.local"
git config user.name "chaos experiment 2"
git fetch -q origin main
git reset -q --hard origin/main

case "$action" in
  break)
    grep -q "newTag: local" "$overlay" || { echo "error: $overlay does not pin newTag: local" >&2; exit 1; }
    sed -i 's/newTag: local$/newTag: bad/' "$overlay"
    git commit -q -am "chaos 2: deploy linkpulse:bad (readiness fails, liveness passes)"
    ;;
  revert)
    git log -1 --format=%s | grep -q "^chaos 2: deploy linkpulse:bad" || { echo "error: HEAD is not the chaos-2 commit: $(git log -1 --format=%s)" >&2; exit 1; }
    git revert --no-edit HEAD >/dev/null
    ;;
  *)
    echo "usage: $0 break|revert" >&2; exit 2 ;;
esac

git push -q origin HEAD:main
git rev-parse --short HEAD | tee "$evidence/$action.rev"
