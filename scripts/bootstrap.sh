#!/usr/bin/env sh
# Host bootstrap for Linux and macOS. Windows: scripts/bootstrap.ps1, the same steps.
#
# This is the one script that runs on the host rather than in a container, because its job
# is to establish the two things everything else assumes: Docker (running, with Compose v2)
# and mise (the task runner). It installs neither Go, nor Terraform, nor kubectl -- every
# task in mise.toml runs those inside a pinned image, which is the whole point. What
# `mise install` fetches from .tool-versions is for hands-on use (jq, k6, kubectl at the
# cluster's version) and is not required for `mise run dev` to work.
#
# Nothing here needs sudo, and nothing here writes outside $HOME: mise installs to
# ~/.local/bin and keeps its tools under ~/.local/share/mise. The system trust store,
# /etc/hosts and the package manager are untouched -- the properties the README claims.
#
#   sh scripts/bootstrap.sh
#   sh scripts/bootstrap.sh --no-pull     # skip pre-pulling the tool images

set -eu

pull=1
for arg in "$@"; do
  case "$arg" in
    --no-pull) pull=0 ;;
    *) echo "usage: $0 [--no-pull]" >&2; exit 2 ;;
  esac
done

say() { printf '==> %s\n' "$*"; }
die() { printf 'bootstrap: %s\n' "$*" >&2; exit 1; }

# ---- Docker -----------------------------------------------------------------------
say "docker"
command -v docker >/dev/null 2>&1 || die "docker is not installed. Docker Desktop (macOS) or Docker Engine (Linux) is the one prerequisite this script does not install: https://docs.docker.com/get-docker/"
docker info >/dev/null 2>&1 || die "docker is installed but the daemon is not reachable. Start Docker Desktop, or on Linux: sudo systemctl start docker (and add your user to the docker group)."
docker compose version >/dev/null 2>&1 || die "'docker compose' (v2, the plugin) is missing. Docker Desktop includes it; on Linux install docker-compose-plugin."
printf '    %s\n' "$(docker --version)" "$(docker compose version)"

# ---- mise -------------------------------------------------------------------------
say "mise"
if ! command -v mise >/dev/null 2>&1; then
  if [ -x "$HOME/.local/bin/mise" ]; then
    PATH="$HOME/.local/bin:$PATH"
  else
    # The official installer: a static binary into ~/.local/bin, verified against the
    # checksum the script carries. Pinned to a version rather than "latest" for the same
    # reason everything else here is pinned.
    say "installing mise to ~/.local/bin"
    curl -fsSL https://mise.run | MISE_VERSION=v2026.9.5 sh
    PATH="$HOME/.local/bin:$PATH"
  fi
fi
command -v mise >/dev/null 2>&1 || die "mise did not end up on PATH; add ~/.local/bin to PATH and re-run"
printf '    %s\n' "$(mise --version)"

# ---- tools from .tool-versions (optional for the container path) --------------------
say "mise install (from .tool-versions; hands-on tools only, every task runs in containers)"
cd "$(dirname "$0")/.."
mise install || die "mise install failed; the container path still works (mise run dev), but hands-on kubectl/jq/k6 will not be on PATH"

# ---- pre-pull the tool images -------------------------------------------------------
# Optional and purely a time-shift: the first `mise run dev` pulls these anyway. Doing it
# here means the cold-start cost is visible as a progress bar now rather than as a task
# that appears hung later.
if [ "$pull" = 1 ]; then
  say "pre-pulling tool images (docker compose --profile tools pull)"
  docker compose --profile tools pull --quiet || die "image pull failed; check network access to Docker Hub, ghcr.io and quay.io"
fi

say "done"
cat <<'EOF'

    mise run dev        bring everything up on k3d and prove it works (15 min cold, 1 min warm)
    mise run verify     the static checks CI runs
    mise tasks          everything else

If your shell did not have mise on PATH before this, activate it for future sessions:
    echo 'eval "$(~/.local/bin/mise activate bash)"' >> ~/.bashrc      # or zsh
EOF
