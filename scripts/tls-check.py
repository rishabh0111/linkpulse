#!/usr/bin/env python3
"""Assert that every ingress serves a certificate from the cluster's own CA -- verified.

Every hostname the cluster publishes is connected to over TLS with that name as SNI, the
chain is verified against the CA exported by `mise run tls-ca`, the certificate's SAN
list is checked to contain the name, its issuer is checked to be the local CA, its expiry
is checked to be more than 14 days away (cert-manager renews a 90-day leaf at 60 days, so
a 30-day threshold would flake at exactly the moment renewal is due), and finally an
HTTPS request is made through the verified connection to prove the Ingress behind it
routes.

Then one negative check: a hostname nobody issued a certificate for must FAIL
verification. Traefik answers unknown names with its own self-signed default, and a
checker that accepted that would accept anything -- this is what proves the CA file is
actually being used to verify rather than merely being present.

Standard library only, like smoke.py and observability-check.py, so it runs in the bare
python image. The ingress is reached by one address with a different SNI per host, for
the reason observability-check.py gives: inside a container localtest.me resolves to the
container's own loopback.

    python3 scripts/tls-check.py --ingress k3d-linkpulse-serverlb --ca certs/ca.crt
    python3 scripts/tls-check.py --ingress 127.0.0.1 --port 8443 --ca certs/ca.crt   # from the host
    python3 scripts/tls-check.py --skip argocd.localtest.me    # the `dev` path, which has no ArgoCD

--skip names a host that is legitimately absent (`dev` deploys with kubectl and never
installs ArgoCD, so its Ingress and certificate do not exist there). A skipped host is
printed as skipped, never counted as passed; the `gitops` path skips nothing.
"""

from __future__ import annotations

import argparse
import http.client
import socket
import ssl
import sys
from datetime import datetime, timedelta, timezone

TIMEOUT = 15
ISSUER_CN = "LinkPulse Local CA"

# host, path to GET through the verified connection, statuses that mean "routed".
# argocd: 200 for the UI. grafana: 200 (anonymous viewer). prometheus/alertmanager: 200
# for the /-/ready endpoints. linkpulse: the readiness probe. The in-cluster name gets a
# certificate too (k8s/manifests/overlays/local/patch-ingress-tls.yaml says why), and it
# routes through the hostless catch-all rule.
HOSTS = [
    ("linkpulse.localtest.me", "/readyz", {200}),
    ("k3d-linkpulse-serverlb", "/readyz", {200}),
    ("argocd.localtest.me", "/", {200}),
    ("grafana.localtest.me", "/api/health", {200}),
    ("prometheus.localtest.me", "/-/ready", {200}),
    ("alertmanager.localtest.me", "/-/ready", {200}),
]
UNKNOWN_HOST = "nobody-issued-this.localtest.me"

failed = 0
passed = 0


def ok(label: str) -> None:
    global passed
    passed += 1
    print(f"  ok    {label}")


def fail(label: str, detail: str = "") -> None:
    global failed
    failed += 1
    print(f"  FAIL  {label}{': ' + detail if detail else ''}")


def san_names(cert: dict) -> list[str]:
    return [value for kind, value in cert.get("subjectAltName", ()) if kind == "DNS"]


def issuer_cn(cert: dict) -> str:
    for rdn in cert.get("issuer", ()):
        for key, value in rdn:
            if key == "commonName":
                return value
    return ""


def not_after(cert: dict) -> datetime:
    # ssl gives "Sep 11 10:00:00 2027 GMT"; %Z accepts the literal GMT.
    return datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)


def check_host(ctx: ssl.SSLContext, address: str, port: int, host: str, path: str, want: set[int]) -> None:
    print(f"{host}")
    try:
        with socket.create_connection((address, port), timeout=TIMEOUT) as raw:
            with ctx.wrap_socket(raw, server_hostname=host) as tls:
                cert = tls.getpeercert()
                ok(f"chain verified against the CA ({tls.version()}, {tls.cipher()[0]})")

                names = san_names(cert)
                if host in names:
                    ok(f"SAN contains {host}  {names}")
                else:
                    fail("SAN does not contain the host", str(names))

                issuer = issuer_cn(cert)
                if issuer == ISSUER_CN:
                    ok(f"issued by {issuer!r}")
                else:
                    fail("unexpected issuer", repr(issuer))

                expiry = not_after(cert)
                left = expiry - datetime.now(timezone.utc)
                if left > timedelta(days=14):
                    ok(f"expires {expiry:%Y-%m-%d} ({left.days} days)")
                else:
                    fail("certificate expires within 14 days", f"{expiry:%Y-%m-%d}")

                # An HTTP request over the SAME verified socket, so "routes" is asserted
                # for the connection that was just checked rather than a fresh one.
                conn = http.client.HTTPSConnection(address, port, context=ctx, timeout=TIMEOUT)
                conn.sock = tls
                conn.request("GET", path, headers={"Host": host})
                resp = conn.getresponse()
                resp.read()
                if resp.status in want:
                    ok(f"GET {path} -> {resp.status}")
                else:
                    fail(f"GET {path} returned {resp.status}", f"wanted one of {sorted(want)}")
    except ssl.SSLCertVerificationError as exc:
        fail("certificate verification failed", exc.verify_message)
    except (OSError, http.client.HTTPException) as exc:
        fail("connection failed", str(exc))


def check_unknown_host_is_refused(ctx: ssl.SSLContext, address: str, port: int) -> None:
    print(f"{UNKNOWN_HOST}  (negative: must NOT verify)")
    try:
        with socket.create_connection((address, port), timeout=TIMEOUT) as raw:
            with ctx.wrap_socket(raw, server_hostname=UNKNOWN_HOST) as tls:
                cert = tls.getpeercert()
                fail("verification unexpectedly succeeded", f"issuer={issuer_cn(cert)!r} san={san_names(cert)}")
    except ssl.SSLCertVerificationError as exc:
        ok(f"refused as expected ({exc.verify_message})")
    except OSError as exc:
        fail("connection failed", str(exc))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ingress", default="k3d-linkpulse-serverlb", help="address to connect to")
    parser.add_argument("--port", type=int, default=443)
    parser.add_argument("--ca", default="certs/ca.crt", help="PEM from `mise run tls-ca`")
    parser.add_argument("--skip", action="append", default=[], metavar="HOST", help="host to skip (repeatable)")
    args = parser.parse_args()
    unknown = [h for h in args.skip if h not in {host for host, _, _ in HOSTS}]
    if unknown:
        parser.error(f"--skip names a host this script does not check: {unknown}")

    try:
        ctx = ssl.create_default_context(cafile=args.ca)
    except (OSError, ssl.SSLError) as exc:
        print(f"tls-check: cannot load CA {args.ca}: {exc}", file=sys.stderr)
        return 2
    # cafile alone: nothing from the system store, so the only way a certificate passes
    # is by chaining to the local CA. check_hostname stays on -- that is the SAN check
    # that the negative case below relies on.
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED

    print(f"tls-check: {args.ingress}:{args.port}, CA {args.ca}\n")
    for host, path, want in HOSTS:
        if host in args.skip:
            print(f"{host}\n  skip  (--skip)\n")
            continue
        check_host(ctx, args.ingress, args.port, host, path, want)
        print()
    check_unknown_host_is_refused(ctx, args.ingress, args.port)

    skipped = f", {len(args.skip)} skipped" if args.skip else ""
    print(f"\ntls-check: {'PASS' if failed == 0 else 'FAIL'} ({passed} ok, {failed} failed{skipped})")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
