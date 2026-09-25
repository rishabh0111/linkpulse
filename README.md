# LinkPulse

[![CI](https://github.com/rishabh0111/linkpulse/actions/workflows/ci.yml/badge.svg)](https://github.com/rishabh0111/linkpulse/actions/workflows/ci.yml)
![Go](https://img.shields.io/badge/Go-00ADD8?style=flat&logo=go&logoColor=white)
![DynamoDB](https://img.shields.io/badge/DynamoDB-4053D6?style=flat&logo=data%3Aimage%2Fsvg%2Bxml%3Bbase64%2CPHN2ZyB2aWV3Qm94PSIwIDAgMTI4IDEyOCIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48cGF0aCBmaWxsPSIjZmZmIiBkPSJNMCAwdjEyOGgxMjhWMEgwem01Ni43NDYgMjEuMjY2YzEwLjA1MiAwIDE4Ljg5NCAxLjk5OCAyNC4wMTQgNS4xMmgtOS4zNzFhNjEuNDQgNjEuNDQgMCAwMC0xNC42NDMtMS43MDZjLTE0LjY0MyAwLTI1LjYwMSA0LjQ4OC0yNS42MDEgOC41MzMgMCA0LjA0NSAxMC45NTggOC41MzMgMjUuNjAxIDguNTMzaDIuMTg2bC0xLjgwOSAzLjQxNGgtLjM3N2MtOS45NjcgMC0yMC4zOC0yLjA4NC0yNS42MDEtNi4wNzh2Ny44ODVjLjAzNCAxLjE2IDEuMDYgMi40OTIgMi44ODYgMy42NyA0LjAyOCAyLjU3NyAxMS4zMTQgNC4zMTYgMTkuMzg3IDQuNzA5bC0uMDg2IDMuNDE0Yy04LjIwOS0uMzc2LTE1LjQ0Mi0yLjAzMy0yMC4xNTItNC42NzhhNC40MDMgNC40MDMgMCAwMC0yLjAzNSAzLjA3NGMwIDQuMDQ1IDEwLjk1OCA4LjUzMyAyNS42MDEgOC41MzMgMS42OSAwIDMuMjc2LS4wNSA0Ljk4My0uMTdsLS45NyAzLjQxNWMtMS4zMTMuMTItMi42NjUuMTE5LTQuMDEzLjExOS05Ljk2NyAwLTIwLjM4LTIuMDgzLTI1LjYwMS02LjA3NnY3LjkwMmMuMDE3IDEuMTQzIDEuMDQzIDIuNDU2IDIuODg2IDMuNjY4IDQuNjI1IDIuOTM1IDEzLjMyOCA0Ljc5NSAyMi43MTUgNC43OTVoMS4xNDNsLS45NTMgMy40MTRoLS4xOWMtOS41MjMgMC0xOC4yMDctMS43NzMtMjMuNTY2LTQuNzZhNC40MDMgNC40MDMgMCAwMC0yLjAzNSAzLjA3YzAgMy44NTcgMTAuMTA0IDguMjEgMjMuODk0IDguNTM0aDEuNzA3YTcyLjA3MyA3Mi4wNzMgMCAwMDkuMTQ5LS42NDdjOS44My0xLjM4MiAxNi40NS00Ljc4MSAxNi40NS03LjkwNGE0LjQwMyA0LjQwMyAwIDAwLTIuMDI5LTMuMDcyIDI3LjcgMjcuNyAwIDAxLTUuODM3IDIuMzczbDExLjI4LTExLjI2NHYxLjYzN2E2LjkzIDYuOTMgMCAwMS0yLjU3NyA1LjExOSA2LjgyNyA2LjgyNyAwIDAxMi41NzggNS4xMnYxMy42NTNhMS4yOCAxLjI4IDAgMDEwIC4zNjJjLS40NjEgNy41MjYtMTUuMTM5IDExLjU1Mi0yOS4wMTQgMTEuNTUycy0yOC41NTMtNC4wMjctMjkuMDE0LTExLjUyYTIuMTMzIDIuMTMzIDAgMDEwLS4zNTdWODFhNi45NDYgNi45NDYgMCAwMTIuNTc3LTUuMTIxIDYuOTMgNi45MyAwIDAxLTIuNTc3LTUuMTJWNTcuMTA2YTYuOTQ2IDYuOTQ2IDAgMDEyLjU3Ny01LjExOSA2LjkzIDYuOTMgMCAwMS0yLjU3Ny01LjAzN1YzMy4yMTNjMC03Ljc4MiAxNC45NTEtMTEuOTQ3IDI5LjAxNC0xMS45NDd6bTEyLjc5OSA4LjUxN2gyMi4xODdhMS43MDcgMS43MDcgMCAwMTEuNzA3IDIuMjMzbC00LjM2NyAxMy4xMjdoOS41NzNhMS43MDcgMS43MDcgMCAwMTEuMjMgMi44ODJMNjIuMzI4IDg3LjI4YTEuNzA3IDEuNzA3IDAgMDEtMS4zMTYuNTggMS43MDcgMS43MDcgMCAwMS0uODU0LS4yNCAxLjcwNyAxLjcwNyAwIDAxLS43ODMtMS45NjNsOS41OS0zMS45M2gtOS42NmExLjcwNyAxLjcwNyAwIDAxLTEuNTE2LTIuNTI3bDEwLjI0LTIwLjQ4YTEuNzA3IDEuNzA3IDAgMDExLjUxNi0uOTM2em0uOTU3IDMuNDNsLTguNDMgMTcuMWg5LjE4YTEuNzA3IDEuNzA3IDAgMDExLjM2Ny42ODMgMS43MDcgMS43MDcgMCAwMS4yNzQgMS41bC04LjIxMSAyNy4zMDcgMjkuODg0LTMxLjIzaC03Ljk1NWExLjcwNyAxLjcwNyAwIDAxLTEuNzA1LTIuMjUzbDQuMzctMTMuMTA3SDcwLjUwMXpNMzYuMjY2IDQ0LjgzMmEyLjEzMyAyLjEzMyAwIDAxMi4xMzIgMi4xMzUgMi4xMzMgMi4xMzMgMCAwMS0yLjEzMiAyLjEzMyAyLjEzMyAyLjEzMyAwIDAxLTIuMTMzLTIuMTMzIDIuMTMzIDIuMTMzIDAgMDEyLjEzMy0yLjEzNXptMCAyMy44OTVhMi4xMzMgMi4xMzMgMCAwMTIuMTMyIDIuMTMyIDIuMTMzIDIuMTMzIDAgMDEtMi4xMzIgMi4xMzMgMi4xMzMgMi4xMzMgMCAwMS0yLjEzMy0yLjEzMyAyLjEzMyAyLjEzMyAwIDAxMi4xMzMtMi4xMzJ6bS01LjEyMSAxOC4xMDl2Ny44NWMwIDQuMDc4IDEwLjk1OCA4LjUzMyAyNS42MDEgOC41MzMgMTQuNjQzIDAgMjUuNi00LjUwNiAyNS42LTguNTMzdi03Ljg1Yy01LjIyMyAzLjk2LTE1LjYzMyA2LjA0MS0yNS42IDYuMDQxLTkuOTY3IDAtMjAuMzgtMi4wODItMjUuNjAxLTYuMDQxem01LjEyIDUuNzgzYTIuMTMzIDIuMTMzIDAgMDEyLjEzMyAyLjEzMyAyLjEzMyAyLjEzMyAwIDAxLTIuMTMyIDIuMTM1IDIuMTMzIDIuMTMzIDAgMDEtMi4xMzMtMi4xMzUgMi4xMzMgMi4xMzMgMCAwMTIuMTMzLTIuMTMzeiIvPjwvc3ZnPgo%3D)
![Kubernetes](https://img.shields.io/badge/Kubernetes-326CE5?style=flat&logo=kubernetes&logoColor=white)
![Amazon EKS](https://img.shields.io/badge/Amazon_EKS-FF9900?style=flat&logo=data%3Aimage%2Fsvg%2Bxml%3Bbase64%2CPHN2ZyB2aWV3Qm94PSIwIDAgMTI4IDEyOCIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KICA8cGF0aCBmaWxsPSIjZmZmIiBkPSJNMTA4LjU5IDI2LjE0OGMtMS44NTIgMC0zLjYyMi4yMTEtNS4zMDUuNzE1LTEuNjg0LjUwNC0zLjExNyAxLjIyMy00LjM3OSAyLjE4OGExMC44MjkgMTAuODI5IDAgMCAwLTMuMDMxIDMuNDUzYy0uNzU3IDEuMzQ4LTEuMTM3IDIuOTA2LTEuMTM3IDQuNjc2IDAgMi4xODcuNzE2IDQuMjUgMi4xMDYgNi4xMDUgMS4zODYgMS44OTUgMy42NiAzLjMyNCA2LjczNCA0LjI5M2w2LjEwNiAxLjg5NWMyLjA2Mi42NzUgMy40OTYgMS4zOTEgNC4yNTQgMi4xOTEuNzU3LjgwMSAxLjEzNiAxLjc2NSAxLjEzNiAyLjk0NSAwIDEuNzI2LS43NTggMy4wNzQtMi4xOTEgNC0xLjQzLjkyNS0zLjQ5MiAxLjM5MS02LjE0NSAxLjM5MS0xLjY4NyAwLTMuMzI4LS4xNjgtNS4wMTEtLjUwNGEyMy4xMDIgMjMuMTAyIDAgMCAxLTQuNjMzLTEuNDc2Yy0uNDIxLS4xNjgtLjgwMS0uMzM2LTEuMDUxLS40MThhMi4zNTcgMi4zNTcgMCAwIDAtLjc1OC0uMTNjLS42MzQgMC0uOTY5LjQyMy0uOTY5IDEuMzA1djIuMTQ5YTIuOTE5IDIuOTE5IDAgMCAwIC4yNTQgMS4xOGMuMTY4LjM4LjYyOS44IDEuMzA1IDEuMTggMS4wOTQuNjI4IDIuNzM0IDEuMTc5IDQuODQgMS42ODMgMi4xMDUuNTA0IDQuMjk3Ljc1OCA2LjQ4NC43NTggMi4xNSAwIDQuMTI5LS4yOTcgNi4wMjQtLjg4MyAxLjgwOC0uNTUxIDMuMzY3LTEuMzA5IDQuNjcyLTIuMzYgMS4zMDQtMS4wMSAyLjMxNi0yLjI3MyAzLjA3NC0zLjcwNy43MTQtMS40MjkgMS4wOTQtMy4wNyAxLjA5NC00Ljg4MiAwLTIuMTg4LS42MzMtNC4xNjgtMS45MzgtNS44OTUtMS4zMDQtMS43MjctMy40OTEtMy4wNzQtNi41MjMtNC4wNDNsLTUuOTgtMS44OTVjLTIuMjMtLjcxMy0zLjc5LTEuNTE2LTQuNjM0LTIuMzE2LS44NC0uNzk3LTEuMjYxLTEuODA4LTEuMjYxLTIuOTg4IDAtMS43MjYuNjcxLTIuOTUgMS45OC0zLjc0NiAxLjMwNS0uODAxIDMuMTk5LTEuMTggNS41OTgtMS4xOCAyLjk4OCAwIDUuNjgzLjU0NyA4LjA4NiAxLjY0LjcxNC4zMzcgMS4yNjEuNTA4IDEuNTk3LjUwOC42MzMgMCAuOTY5LS40NjMuOTY5LTEuMzQ3di0xLjk4YzAtLjU5LS4xMjUtMS4wNTEtLjM3OS0xLjM5MS0uMjUtLjM3OC0uNjcyLS43MTUtMS4yNjItMS4wNTEtLjQyMi0uMjU0LTEuMDExLS41MDQtMS43Ny0uNzU4YTMyLjUyOCAzMi41MjggMCAwIDAtMi4zOTgtLjY3NmMtLjg4Ni0uMTY4LTEuNzY5LS4zMzYtMi43MzgtLjQ2YTIxLjM0NyAyMS4zNDcgMCAwIDAtMi44Mi0uMTY5em0tODYuODIyLjA4MmMtMi4zMTYgMC00LjUwOC4yNTQtNi41Ny44MDEtMi4wNjMuNTA1LTMuODMxIDEuMTM3LTUuMzAzIDEuODk1LS41OS4yOTctLjk3LjU5LTEuMTguODgzLS4yMTEuMjk2LS4yOTMuOC0uMjkzIDEuNDc2djIuMDYzYzAgLjg4Mi4yOTMgMS4zMDQuODgzIDEuMzA0LjE2OCAwIC4zNzgtLjA0My42NzQtLjEyNS4yOTMtLjA4Ni43OTYtLjI1NCAxLjQ3Mi0uNTQ3YTMzLjQxNiAzMy40MTYgMCAwIDEgNC41NDctMS40MzNBMTkuMTc2IDE5LjE3NiAwIDAgMSAyMC41NDcgMzJjMy4yNDIgMCA1LjUxMy42MzMgNi44NjMgMS45MzggMS4zMDQgMS4zMDMgMS45OCAzLjUzNCAxLjk4IDYuNzM0djMuMDc0Yy0xLjY4My0uMzc5LTMuMjgzLS43MTUtNC44NDMtLjkyNi0xLjU1OC0uMjEtMy4wMzEtLjMzNi00LjQ2MS0uMzM2LTQuMzQgMC03Ljc1IDEuMDk0LTEwLjMxNiAzLjI4Ni0yLjU3MSAyLjE4Ny0zLjgzMiA1LjA5My0zLjgzMiA4LjY3MSAwIDMuMzY4IDEuMDUgNi4wNjMgMy4xMTMgOC4wODYgMi4wNjYgMi4wMiA0Ljg4NyAzLjAzMiA4LjQyMiAzLjAzMiA0Ljk3IDAgOS4wOTctMS45MzggMTIuMzc5LTUuODEzYTM0LjE1MyAzNC4xNTMgMCAwIDAgMS4zMDQgMi40ODQgMTMuMjggMTMuMjggMCAwIDAgMS41MTYgMS45OGMuNDIyLjM4Ljg0NC41OSAxLjI2Ni41OS4zMzQgMCAuNzE0LS4xMjggMS4wOTMtLjM3OGwyLjY1My0xLjc3Yy41NDYtLjQyLjgtLjg0My44LTEuMjYxYTEuODYgMS44NiAwIDAgMC0uMjkzLS45NyAyMi40NjkgMjIuNDY5IDAgMCAxLTEuMzQ3LTMuMDNjLS4yOTctLjkyNS0uNDY1LTIuMTktLjQ2NS0zLjc1aC0uMDg2VjQwYzAtNC42MzMtMS4xNzYtOC4wODYtMy40OTItMTAuMzYtMi4zNi0yLjI3My02LjAyNS0zLjQxLTExLjAzMy0zLjQxem0xOS41OCAxLjAxMmMtLjY3NiAwLTEuMDEyLjM3OS0xLjAxMiAxLjA1MSAwIC4yOTcuMTI5Ljg0NC4zNzkgMS42ODdsOS44OTQgMzIuNTQ3Yy4yNTQuOC41NDcgMS4zODcuODg3IDEuNjQxLjMzNi4yOTcuODQuNDIyIDEuNTk4LjQyMmgzLjYyYy43NTkgMCAxLjM0Ny0uMTI1IDEuNjg0LS40MjIuMzQtLjI5My41OTEtLjg0LjgwMS0xLjY4NGw2LjQ4NS0yNy4xMTcgNi41MjcgMjcuMTZjLjE2OC44NC40NiAxLjM4Ny44IDEuNjg0LjMzNy4yOTIuODgzLjQyMiAxLjY4NC40MjJoMy42MjFjLjcxNSAwIDEuMjYyLS4xNjcgMS41OTgtLjQyMi4zNC0uMjUzLjYzMy0uOC44ODctMS42NEw5MC45NDkgMzAuMDJjLjE2OC0uNDYuMjUtLjc5Ny4yOTMtMS4wNTEuMDQzLS4yNTQuMDg2LS40NjYuMDg2LS42NzYgMC0uNzE1LS4zNzktMS4wNS0xLjA1NS0xLjA1SDg2LjM2Yy0uNzU3IDAtMS4zMDguMTY2LTEuNjQ0LjQyMS0uMjkzLjI1LS41OS44LS44NCAxLjY0TDc2LjU5IDU3LjUxN2wtNi42NTMtMjguMjExYy0uMTY2LS44LS40NjQtMS4zOS0uOC0xLjY0LS4zMzYtLjI5OC0uODg0LS40MjMtMS42ODQtLjQyM2gtMy4zNjdjLS43NTggMC0xLjM0OC4xNjctMS42ODguNDIyLS4zMzUuMjUtLjU4OC44LS43OTYgMS42NGwtNi41NyAyNy44NzYtNy4wNzUtMjcuODc1Yy0uMjUtLjgtLjUwNC0xLjM5LS44NC0xLjY0LS4yOTctLjI5OC0uODQ0LS40MjMtMS42NDQtLjQyM2gtNC4xMjV6TTIxLjY0IDQ3LjQ5NmEzMS44MTYgMzEuODE2IDAgMCAxIDMuOTYuMjUgMzQuNDAxIDM0LjQwMSAwIDAgMSAzLjg3Mi43MTl2MS43NjVjMCAxLjQzNS0uMTY4IDIuNjUzLS40MjIgMy42NjUtLjI1IDEuMDEtLjc1OCAxLjg5NS0xLjQzIDIuNjk1LTEuMTM3IDEuMjYyLTIuNDg0IDIuMTg3LTQgMi42OTUtMS41MTYuNTA0LTIuOTQ5Ljc1OC00LjMzNi43NTgtMS45MzcgMC0zLjQxLS41MDgtNC40MjItMS41NTktMS4wNTQtMS4wMS0xLjU1OC0yLjQ4NC0xLjU1OC00LjQ2NCAwLTIuMTA2LjY3NS0zLjcwNCAyLjA2Mi00Ljg0IDEuMzkxLTEuMTM3IDMuNDU0LTEuNjg0IDYuMjc0LTEuNjg0ek0xMTggNzMuMzQ4Yy00LjQzMi4wNjMtOS42NjQgMS4wNTItMTMuNjIxIDMuODMyLTEuMjIzLjg4My0xLjAxMiAyLjA2Mi4zMzYgMS44OTQgNC41MDgtLjU0NyAxNC40NC0xLjcyNiAxNi4yMS41NDcgMS43NyAyLjIzLTEuOTc2IDExLjYyLTMuNjYzIDE1Ljc5LS41MDQgMS4yNi41OSAxLjc2OSAxLjcyNi44IDcuNDEtNi4yMzEgOS4zNDgtMTkuMjQyIDcuODMyLTIxLjEzNy0uNzU3LS45MjUtNC4zODgtMS43OS04LjgyLTEuNzI2ek0xLjYzIDc1Ljg1OWMtLjkyNi4xMTYtMS4zNDcgMS4yMzYtLjM2OCAyLjEyMSAxNi41MDggMTQuOTAyIDM4LjM1OSAyMy44NzIgNjIuNjEzIDIzLjg3MiAxNy4zMDUgMCAzNy40My01LjQzIDUxLjI4MS0xNS42NiAyLjI3My0xLjY4OS4yOTgtNC4yNTQtMi4wMi0zLjIwNC0xNS41MzMgNi41Ny0zMi40MjEgOS43Ny00Ny43ODggOS43Ny0yMi43NzggMC00NC44LTYuMjczLTYyLjY1My0xNi42MzMtLjM5LS4yMzEtLjc1NS0uMzA0LTEuMDY0LS4yNjZ6Ii8%2BCjwvc3ZnPgo%3D)
![Argo CD](https://img.shields.io/badge/Argo_CD-EF7B4D?style=flat&logo=argo&logoColor=white)
![Terraform](https://img.shields.io/badge/Terraform-844FBA?style=flat&logo=terraform&logoColor=white)
![Prometheus](https://img.shields.io/badge/Prometheus-E6522C?style=flat&logo=prometheus&logoColor=white)
![Grafana](https://img.shields.io/badge/Grafana-F46800?style=flat&logo=grafana&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ED?style=flat&logo=docker&logoColor=white)

**[Full write-up](https://rishabh0111.github.io/blogs/production-grade-devops-platform/)** ·
[Chaos evidence](docs/evidence/) ·
[The EKS run](docs/evidence/burst/README.md) ·
[Data model](docs/data-model.md) ·
[Runbook](docs/runbook.md) ·
[CI runs](https://github.com/rishabh0111/linkpulse/actions/workflows/ci.yml)

A URL shortener with click analytics, and the platform around it. A Go service over a
single DynamoDB table, deployed to Kubernetes by ArgoCD, with Terraform for four
environments, a Prometheus and Loki monitoring stack, and five chaos experiments that
induce the failures its alerts claim to catch. It runs on k3d with LocalStack locally and
in CI, and has run on EKS against real DynamoDB.

## Running it

```sh
sh scripts/bootstrap.sh       # Windows: powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1
mise run dev
```

Needs Docker (Desktop or Engine, Compose v2) and [mise](https://mise.jdx.dev), which the
bootstrap script installs into your home directory. Everything else, Go, Terraform,
kubectl, k6 and Python, runs in pinned containers. Nothing system-wide changes: no hosts
file, no trust store, no sudo. On SELinux hosts the socket-mounting services run
unlabelled and the repo mounts use `:z`.

`dev` starts LocalStack, applies the Terraform, creates a three-node k3d cluster, installs
cert-manager and Sealed Secrets, deploys the application and monitoring, then checks it:
the smoke test over verified TLS, the 29-assertion observability check, the TLS check on
every hostname and a Sealed Secrets round trip. About fifteen minutes cold, one warm.

| | |
| --- | --- |
| App | http://linkpulse.localtest.me:8088, https://linkpulse.localtest.me:8443 |
| Grafana | http://grafana.localtest.me:8088 (anonymous viewer; `admin`/`admin` to edit) |
| Prometheus | http://prometheus.localtest.me:8088 |
| Alertmanager | http://alertmanager.localtest.me:8088 |

`localtest.me` resolves to `127.0.0.1` in public DNS. `mise run tls-ca` exports the
cluster's CA to `certs/ca.crt` if a browser should trust the `https://` names.

## Try it

```sh
API=http://linkpulse.localtest.me:8088

# Shorten a URL
curl -s $API/graphql -H 'Content-Type: application/json' \
  -d '{"query":"mutation { shortenUrl(longUrl: \"https://example.com/a/long/path\") { code shortUrl } }"}'
# -> {"data":{"shortenUrl":{"code":"<CODE>","shortUrl":".../r/<CODE>"}}}

# Follow it: one read, then 302. The click is recorded afterwards, off the request path.
curl -si $API/r/<CODE> | head -3

# Read the analytics
curl -s $API/graphql -H 'Content-Type: application/json' \
  -d '{"query":"{ link(code: \"<CODE>\") { totalClicks clicksByDay { day count } recentClicks(limit: 5) { at country uaClass } } }"}'
```

`shortenUrl` refuses `javascript:` and other non-HTTP schemes with a `400`. An unknown or
malformed code answers `404`.

## API

| | |
| --- | --- |
| `GET /r/{code}` | Redirect: one DynamoDB read, `302`, click enqueued for eight background writers |
| `POST /graphql` | `shortenUrl`, `link(code)`, `links(ownerId)` with `totalClicks`, `clicksByDay`, `recentClicks` |
| `GET /graphql` | Queries only; mutations over `GET` are refused |
| `GET /healthz` | Liveness: the process, never the datastore |
| `GET /readyz` | Readiness: a `DescribeTable`, cached 2 s |
| `GET /metrics` | Prometheus |
| `GET /` | The dashboard |

## The claims, and the evidence behind each

| Claim | Evidence |
| --- | --- |
| A redirect never waits for its click to be written | [`TestRedirectDoesNotBlockOnClickWrite`](app/graphql-api/internal/httpapi/router_test.go) |
| When the click queue is full, clicks are shed and counted, and redirects carry on | [`TestClickQueueShedsWhenFull`](app/graphql-api/internal/httpapi/router_test.go) |
| DynamoDB throttling costs analytics, never redirects: zero 5xx, redirect p99 unchanged | Experiment 1: [locally, injected](docs/evidence/chaos-1/timeline.md); [on real DynamoDB, not injected](docs/evidence/burst/chaos-1/timeline.md), with [CloudWatch's view](docs/evidence/burst/chaos-1/cloudwatch-throttling.png) |
| Losing the datastore makes pods unready and never restarts them | [`TestLivenessIgnoresStoreOutage`](app/graphql-api/internal/httpapi/router_test.go), [`TestReadinessFailsOnStoreOutage`](app/graphql-api/internal/httpapi/router_test.go), experiment 3 ([timeline](docs/evidence/chaos-3/timeline.md)) |
| A deploy that never becomes ready never replaces the pods serving traffic, and `git revert` rolls it back | Experiment 2 ([timeline](docs/evidence/chaos-2/timeline.md)), through ArgoCD with nobody touching the cluster |
| A memory leak is OOM-killed, alerted on, and caught as CrashLoopBackOff while the other replica serves | Experiment 4 ([timeline](docs/evidence/chaos-4/timeline.md)) |
| A drain respects the disruption budget; a lost node is reported and its pod replaced | Experiment 5 ([timeline](docs/evidence/chaos-5/timeline.md)) |
| Every alert rule can fire, and fires on the failure it names | The five experiments assert each rule by inducing its condition; rules in [`monitoring/prometheus/rules/`](monitoring/prometheus/rules/) |
| The monitoring stack is observing: targets up, rules evaluating, logs and dashboards queryable | [`scripts/observability-check.py`](scripts/observability-check.py), 29 assertions |
| The click aggregate is sharded, and a day's range read covers every shard | [`TestWriteShardingSpreadsAggregateKeys`](app/graphql-api/internal/store/keys_test.go), [`TestStatRangeCoversAllShards`](app/graphql-api/internal/store/keys_test.go) |
| Dangerous URLs are refused | [`TestShortenRejectsDangerousURLs`](app/graphql-api/internal/gql/schema_test.go) |
| The always-free environment has nothing billing by the hour, and its capacity cannot exceed the free allowance | [`scripts/metered-resources.py`](scripts/metered-resources.py) `--expect-none`; the table module validates the table and index sum to 25 per side |
| A backup restores the table byte for byte after it is destroyed | [`scripts/backup.py`](scripts/backup.py) `verify`, in CI after `terraform destroy` and re-apply |
| Every ingress serves a certificate from the cluster's own CA | [`scripts/tls-check.py`](scripts/tls-check.py) |
| A value sealed offline decrypts only in the cluster holding the key | [`scripts/seal-check.sh`](scripts/seal-check.sh) |
| The same manifests run on EKS against real DynamoDB, with IRSA and no stored credential | [The EKS run](docs/evidence/burst/README.md): smoke, observability 29/29, k6 baseline, experiment 1 |

CI runs the unit tests, every static check, and on a three-node cluster the smoke,
observability, TLS and Sealed Secrets checks, the k6 baseline, experiments 1, 3, 4 and 5,
and the backup restore, on every push. Experiment 2 needs the GitOps path:
`mise run gitops && mise run chaos-2`.

## Development

```sh
mise run app-test             # gofmt, vet, unit tests
mise run tf-check             # terraform fmt and validate, every environment
mise run k8s-render           # render every kustomization
mise run gitops               # Gitea as the remote, ArgoCD deploying from it
mise run k6-baseline          # two minutes of load; thresholds are the verdict
mise run chaos-3              # or chaos-1 .. chaos-5, or chaos-all after gitops
mise run clean                # remove the cluster and all local state
mise tasks                    # everything, with descriptions
```

`make <task>` forwards to `mise run <task>`. Every task is one tool invocation in a
container, so they behave the same on Linux, macOS and Windows.

## Configuration

The service reads its environment. Defaults are in
[`internal/config/config.go`](app/graphql-api/internal/config/config.go).

| Variable | Default | |
| --- | --- | --- |
| `DDB_TABLE` | `linkpulse` | Table name |
| `DDB_ENDPOINT` | empty | Empty is real AWS; LocalStack locally |
| `AWS_REGION` | `ap-south-1` | |
| `PUBLIC_BASE_URL` | | Base of `shortUrl` |
| `CLICK_SHARDS` | `16` | Shards per day for the click aggregate |
| `CLICK_WORKERS` | `8` | Background click writers |
| `CLICK_QUEUE_SIZE` | `2048` | Clicks buffered before shedding |
| `CLICK_TTL_DAYS` | `30` | Click records expire after this |
| `READY_CACHE_TTL` | `2s` | How long a readiness result is reused |
| `SHUTDOWN_GRACE` | `15s` | Drain time on `SIGTERM` |
| `PORT` | `8080` | |

`LINKPULSE_ENABLE_DEBUG_LEAK` and `LINKPULSE_BREAK_READINESS` exist for experiments 4
and 2. Only the local overlay and the deliberately broken image set them.

## Deploying to AWS

Terraform environments under [`infra/terraform/envs/`](infra/terraform/envs/): `bootstrap`
(the state bucket), `aws` (always free: the table, IAM, a VPC with no NAT, budgets) and
`aws-burst` (EKS, in its own state so `destroy` cannot reach the table).
[`docs/burst-runbook.md`](docs/burst-runbook.md) is the procedure, including the teardown
that waits for the load balancer to be released before destroying the cluster.

## Further reading

Why it is built this way, and what running it on AWS found:
[the full write-up](https://rishabh0111.github.io/blogs/production-grade-devops-platform/).

The design the rest serves: [`docs/data-model.md`](docs/data-model.md). The diagram:
[`docs/architecture.md`](docs/architecture.md). What to do when each alert fires:
[`docs/runbook.md`](docs/runbook.md). An incident with its own alert rules:
[`docs/postmortem.md`](docs/postmortem.md). Every decision and verification, in order:
[`tracker.md`](tracker.md).

The vendored manifests under `*/upstream/` (ArgoCD, cert-manager, Sealed Secrets,
metrics-server) are unmodified copies of their releases, under their own Apache-2.0
licences.
