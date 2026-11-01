#!/usr/bin/env python3
"""Check the candidate origin over verified TLS, including existing auth routes."""

import argparse
import hashlib
import http.client
import ipaddress
import re
import socket
from urllib.parse import urlsplit

ROUTES = {
    "/": (200, b"Agent Council"),
    "/en/": (200, b"Agent Council"),
    "/login?next=%2Fprofiles": (200, None),
    "/api/config": (401, None),
}


def check(base, *, timeout=5, origin_address=None, expected_pages=None):
    url = urlsplit(base)
    if (
        url.scheme not in ("http", "https")
        or not url.hostname
        or url.username
        or url.password
        or url.path not in ("", "/")
        or url.query
        or url.fragment
    ):
        raise ValueError("Invalid origin URL")
    if origin_address:
        ipaddress.ip_address(origin_address)
        if url.scheme != "https":
            raise ValueError("Direct-origin checks require verified HTTPS")
    if expected_pages is not None and (
        set(expected_pages) != {"/", "/en/"}
        or any(not re.fullmatch(r"[a-f0-9]{64}", value) for value in expected_pages.values())
    ):
        raise ValueError("Expected both candidate page hashes")
    for path, (expected, marker) in ROUTES.items():
        connection_type = (
            http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
        )
        connection = connection_type(url.hostname, url.port, timeout=timeout)
        if origin_address:
            # HTTPSConnection retains the real hostname for Host and TLS SNI /
            # certificate validation; only the socket destination changes.
            connection._create_connection = lambda address, timeout, source_address=None: (
                socket.create_connection((origin_address, address[1]), timeout, source_address)
            )
        try:
            connection.request("GET", path, headers={"Cache-Control": "no-cache"})
            response = connection.getresponse()
            data = response.read(1024 * 1024 + 1)
            if response.status != expected:
                raise RuntimeError(f"{path}: unexpected HTTP {response.status}")
            if len(data) > 1024 * 1024 or (marker is not None and marker not in data):
                raise RuntimeError(f"{path}: invalid public content")
            if expected_pages and path in expected_pages:
                if hashlib.sha256(data).hexdigest() != expected_pages[path]:
                    raise RuntimeError(f"{path}: origin is not serving the candidate artifact")
            if path in ("/", "/en/"):
                if response.getheader("X-Content-Type-Options") != "nosniff":
                    raise RuntimeError(f"{path}: missing content-type protection")
                if response.getheader("Referrer-Policy") != "same-origin":
                    raise RuntimeError(f"{path}: missing referrer policy")
        finally:
            connection.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="https://council.zavarov.com")
    parser.add_argument("--origin-address")
    parser.add_argument("--ru-sha256")
    parser.add_argument("--en-sha256")
    args = parser.parse_args()
    hashes = None
    if args.ru_sha256 or args.en_sha256:
        if not args.ru_sha256 or not args.en_sha256 or not args.origin_address:
            parser.error("Candidate verification requires both page hashes and a direct origin")
        hashes = {"/": args.ru_sha256, "/en/": args.en_sha256}
    check(args.base_url, origin_address=args.origin_address, expected_pages=hashes)
    print("Council landing and login HTTP smoke checks passed")


if __name__ == "__main__":
    main()
