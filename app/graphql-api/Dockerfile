# syntax=docker/dockerfile:1.7
#
# Multi-stage, multi-arch, non-root, distroless. The result is a ~15 MB image containing
# one static binary and nothing else — no shell, no package manager, no libc — which is
# what makes the Trivy HIGH/CRITICAL gate in CI passable rather than aspirational: an
# image with no OS packages has no OS CVEs.

ARG GO_VERSION=1.27.1

# BUILDPLATFORM pins the builder to the native architecture of the machine doing the
# build, and Go cross-compiles to TARGETARCH from there. Emulating the target
# architecture under QEMU to run a native compiler would be several times slower for no
# benefit — Go does not need it.
FROM --platform=$BUILDPLATFORM golang:${GO_VERSION}-alpine AS build

WORKDIR /src

# Dependencies are copied and downloaded before the source, so an application change does
# not invalidate the module layer.
COPY go.mod go.sum ./
RUN --mount=type=cache,target=/go/pkg/mod \
    go mod download

COPY . .

ARG TARGETOS
ARG TARGETARCH
ARG VERSION=dev

# CGO_ENABLED=0 is what makes the binary static and so runnable on distroless/static.
# -trimpath keeps build-host paths out of the binary; -s -w drop the symbol table and
# DWARF, which is most of the size difference.
RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    CGO_ENABLED=0 GOOS=${TARGETOS} GOARCH=${TARGETARCH} \
    go build -trimpath -ldflags "-s -w" -o /out/server ./cmd/server

# Run the tests in the build so a broken commit cannot produce an image at all. CI runs
# them separately too, for the readable report; this is the backstop for a local build.
RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    CGO_ENABLED=0 go test ./...

# ---

FROM gcr.io/distroless/static:nonroot

# BREAK_READINESS builds the deliberately broken image used by chaos experiment 2. The
# default is empty, so a normal build can never accidentally produce it, and the bad image
# is a genuinely distinct artifact with its own digest rather than a runtime flag someone
# might leave set. scripts/chaos/02-bad-deploy.sh builds it with --build-arg
# BREAK_READINESS=true.
ARG BREAK_READINESS=""
ARG VERSION=dev

ENV LINKPULSE_BREAK_READINESS=${BREAK_READINESS} \
    LINKPULSE_VERSION=${VERSION} \
    PORT=8080

COPY --from=build /out/server /server

# 65532 is distroless's `nonroot` user. Set numerically so the manifests' runAsUser and
# runAsNonRoot checks can assert on it without resolving a name that does not exist in an
# image with no /etc/passwd lookups.
USER 65532:65532

EXPOSE 8080

# No HEALTHCHECK: Kubernetes owns liveness and readiness here (k8s/manifests), and a
# distroless image has no shell or curl to run one with anyway.
ENTRYPOINT ["/server"]
