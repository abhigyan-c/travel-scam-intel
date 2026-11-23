"""Read-only external checks for the public HTTPS preview; run from outside the VPS."""

import argparse
import http.client
import ipaddress
import json
import ssl


def request(ip, path, *, method="GET", secure=True, payload=None):
    connection = (
        http.client.HTTPSConnection(ip, timeout=10, context=ssl.create_default_context())
        if secure
        else http.client.HTTPConnection(ip, timeout=10)
    )
    try:
        headers = {}
        body = None
        if payload is not None:
            body = json.dumps(payload)
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            }
        connection.request(method, path, body=body, headers=headers)
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
    status, headers, body = request(
        ip,
        "/mcp",
        method="POST",
        payload={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "https-preview-check", "version": "1"},
            },
        },
    )
    if status != 200 or "tools" not in json.loads(body).get("result", {}).get("capabilities", {}):
        raise ValueError("mcp_initialization_failed")
    return {
        "trusted_ip_tls": "passed",
        "certificate_expires": certificate["notAfter"],
        "public_health": "passed",
        "private_routes_blocked": len(blocked),
        "public_rest_write_blocked": True,
        "http_application_blocked": True,
        "mcp_route": "initialized",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("public_ip")
    args = parser.parse_args()
    print(json.dumps(verify(args.public_ip), indent=2))


if __name__ == "__main__":
    main()
