#!/usr/bin/env python3
"""Read-only HTTP smoke checks after switching the public Council landing."""

import argparse
import http.client
from urllib.parse import urlsplit

ROUTES = {
    "/": (200, b"Agent Council"),
    "/en/": (200, b"Agent Council"),
    "/login?next=%2Fprofiles": (200, None),
    "/api/config": (401, None),
}


def check(base, *, timeout=5):
    url = urlsplit(base)
    if url.scheme not in ("http", "https") or not url.hostname:
        raise ValueError("Invalid URL")
    for path, (expected, marker) in ROUTES.items():
        if url.scheme == "https":
            connection = http.client.HTTPSConnection(url.hostname, url.port, timeout=timeout)
        else:
            connection = http.client.HTTPConnection(url.hostname, url.port, timeout=timeout)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            data = response.read(1024 * 1024)
            if response.status != expected:
                raise RuntimeError(f"{path}: unexpected HTTP {response.status}")
            if marker is not None and marker not in data:
                raise RuntimeError(f"{path}: missing expected content")
        finally:
            connection.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="https://council.zavarov.com")
    args = parser.parse_args()
    check(args.base_url)
    print("Council landing and login HTTP smoke checks passed")


if __name__ == "__main__":
    main()
