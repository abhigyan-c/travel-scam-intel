"""Read-only external checks for the public HTTPS preview; run from outside the VPS."""

import argparse
import http.client
import ipaddress
import json
import ssl


def request(ip, path, *, method="GET", secure=True):
    connection = (
        http.client.HTTPSConnection(ip, timeout=10, context=ssl.create_default_context())
        if secure
        else http.client.HTTPConnection(ip, timeout=10)
    )
    try:
        connection.request(method, path)
        response = connection.getresponse()
        body = response.read(65536)
        return response.status, dict(response.getheaders()), body
    finally:
        connection.close()


def verify(ip):
    ipaddress.IPv4Address(ip)
    connection = http.client.HTTPSConnection(ip, timeout=10, context=ssl.create_default_context())
    try:
        connection.connect()
        certificate = connection.sock.getpeercert()
    finally:
        connection.close()
    status, headers, body = request(ip, "/health")
    if status != 200 or json.loads(body) != {"status": "ok"}:
        raise ValueError("public_health_failed")
    blocked = [
        "/",
        "/ready",
        "/docs",
        "/openapi.json",
        "/v1/locations?q=Bangkok",
        "/v1/admin/emerging-patterns",
        "/v1/admin/reports/00000000-0000-4000-8000-000000000001/attachments",
    ]
    for path in blocked:
        status, headers, _ = request(ip, path)
        if status != 404 or any(key.lower() == "x-request-id" for key in headers):
            raise ValueError("private_route_reached_application")
    if request(ip, "/v1/reports", method="POST")[0] != 404:
        raise ValueError("public_report_write_exposed")
    if request(ip, "/health", secure=False)[0] != 503:
        raise ValueError("plaintext_application_exposed")
    # /mcp is reserved now; its application-level 404 is expected before PR 31.
    status, headers, _ = request(ip, "/mcp", method="POST")
    if status != 404 or not any(key.lower() == "x-request-id" for key in headers):
        raise ValueError("mcp_preview_route_not_forwarded_or_adapter_now_present")
    return {
        "trusted_ip_tls": "passed",
        "certificate_expires": certificate["notAfter"],
        "public_health": "passed",
        "private_routes_blocked": len(blocked),
        "public_rest_write_blocked": True,
        "http_application_blocked": True,
        "mcp_route": "forwarded; adapter not implemented",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("public_ip")
    args = parser.parse_args()
    print(json.dumps(verify(args.public_ip), indent=2))


if __name__ == "__main__":
    main()
