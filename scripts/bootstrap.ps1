# Host bootstrap for Windows. Linux and macOS: scripts/bootstrap.sh, the same steps.
#
# The one place this project has two implementations of a thing, and why: this script's
# job is to establish the task runner, so it cannot itself be a task, and a POSIX shell is
# not a prerequisite on Windows -- which is the claim the whole repository is built to
# keep. Both files do exactly four things and are short enough to compare by eye:
# check Docker, get mise, `mise install`, pre-pull the tool images.
#
# Nothing here needs administrator rights and nothing writes outside the user profile:
# mise goes to ~\.local\bin (winget or a direct download), its tools under ~\AppData.
#
#   powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1 -NoPull

param([switch]$NoPull)

$ErrorActionPreference = "Stop"
function Say($msg) { Write-Host "==> $msg" }
function Die($msg) { Write-Error "bootstrap: $msg"; exit 1 }

# ---- Docker -----------------------------------------------------------------------
Say "docker"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
  Die "docker is not installed. Docker Desktop is the one prerequisite this script does not install: https://docs.docker.com/desktop/setup/install/windows-install/"
}
docker info *> $null
if ($LASTEXITCODE -ne 0) { Die "docker is installed but the daemon is not reachable. Start Docker Desktop and wait for it to report 'running'." }
docker compose version *> $null
if ($LASTEXITCODE -ne 0) { Die "'docker compose' (v2) is missing; Docker Desktop includes it, so this usually means a very old Desktop." }
Write-Host "    $(docker --version)"
Write-Host "    $(docker compose version)"

# ---- mise -------------------------------------------------------------------------
Say "mise"
$miseBin = Join-Path $HOME ".local\bin"
if (-not (Get-Command mise -ErrorAction SilentlyContinue)) {
  if (Test-Path (Join-Path $miseBin "mise.exe")) {
    $env:PATH = "$miseBin;$env:PATH"
  } elseif (Get-Command winget -ErrorAction SilentlyContinue) {
    # winget is present on every supported Windows 10/11 and installs per-user without
    # elevation. Pinned, like everything else.
    Say "installing mise with winget"
    winget install --id jdx.mise --version 2026.9.5 --exact --accept-source-agreements --accept-package-agreements --silent
    if ($LASTEXITCODE -ne 0) { Die "winget install of jdx.mise failed" }
    # winget updates the user PATH in the registry, not in this session.
    $env:PATH = [Environment]::GetEnvironmentVariable("PATH", "User") + ";" + [Environment]::GetEnvironmentVariable("PATH", "Machine")
  } else {
    # No winget (a locked-down machine, or Server): fetch the release binary directly.
    Say "installing mise to $miseBin"
    New-Item -ItemType Directory -Force $miseBin | Out-Null
    $arch = if ([Environment]::Is64BitOperatingSystem) { "x64" } else { Die "32-bit Windows is not supported" }
    $zip = Join-Path $env:TEMP "mise.zip"
    Invoke-WebRequest -Uri "https://github.com/jdx/mise/releases/download/v2026.9.5/mise-v2026.9.5-windows-$arch.zip" -OutFile $zip
    Expand-Archive -Path $zip -DestinationPath $env:TEMP\mise-unzip -Force
    Copy-Item (Get-ChildItem -Recurse -Filter mise.exe $env:TEMP\mise-unzip | Select-Object -First 1).FullName (Join-Path $miseBin "mise.exe")
    $env:PATH = "$miseBin;$env:PATH"
  }
}
if (-not (Get-Command mise -ErrorAction SilentlyContinue)) { Die "mise did not end up on PATH; add $miseBin to PATH and re-run" }
Write-Host "    $(mise --version)"

# ---- tools from .tool-versions (optional for the container path) --------------------
Say "mise install (from .tool-versions; hands-on tools only, every task runs in containers)"
Set-Location (Join-Path $PSScriptRoot "..")
mise install
if ($LASTEXITCODE -ne 0) { Die "mise install failed; the container path still works (mise run dev), but hands-on kubectl/jq/k6 will not be on PATH" }

# ---- pre-pull the tool images -------------------------------------------------------
if (-not $NoPull) {
  Say "pre-pulling tool images (docker compose --profile tools pull)"
  docker compose --profile tools pull --quiet
  if ($LASTEXITCODE -ne 0) { Die "image pull failed; check network access to Docker Hub, ghcr.io and quay.io" }
}

Say "done"
Write-Host @"

    mise run dev        bring everything up on k3d and prove it works (15 min cold, 1 min warm)
    mise run verify     the static checks CI runs
    mise tasks          everything else

mise runs tasks through cmd.exe on Windows. Every task in mise.toml is written to work there
(single tool invocations, `A || B` as the only control flow) -- see the tracker's loose ends
for what is verified on this platform and what is not.
"@
