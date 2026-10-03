"""
The config.yaml that ships with the project must be safe to run as-is.
"""

from pathlib import Path
from urllib.parse import urlparse

import pytest
import yaml

from ocpp_broker.config import load_config

SHIPPED = Path(__file__).resolve().parents[1] / "config.yaml"

# Hosts that cannot route to anybody's real system (RFC 2606 + loopback).
SAFE_SUFFIXES = (".example.com", ".example.org", ".example.net", ".invalid", ".test", ".localhost")
SAFE_HOSTS = {"example.com", "example.org", "example.net", "localhost", "127.0.0.1", "::1"}


def _urls(node):
    """Every string value under a ``url`` key, anywhere in the document."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "url" and isinstance(value, str):
                yield value
            else:
                yield from _urls(value)
    elif isinstance(node, list):
        for item in node:
            yield from _urls(item)


def test_shipped_config_points_only_at_placeholder_hosts():
    urls = list(_urls(yaml.safe_load(SHIPPED.read_text(encoding="utf-8"))))
    assert urls, "expected the example organization to show a backend url"

    real = []
    for url in urls:
        host = (urlparse(url).hostname or "").lower()
        if host not in SAFE_HOSTS and not host.endswith(SAFE_SUFFIXES):
            real.append(url)

    assert not real, f"config.yaml must not ship a real endpoint (charger traffic would go there): {real}"


def test_shipped_config_loads():
    cfg = load_config(str(SHIPPED))
    assert cfg["organizations"], "the example organization should survive loading"


@pytest.mark.parametrize("needle", ["ev-opt", "ocppmgl", "EDMS", "SLTEST"])
def test_vendor_identifiers_are_gone(needle):
    assert needle not in SHIPPED.read_text(encoding="utf-8")
