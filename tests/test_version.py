"""
The package version has exactly one source: the installed distribution metadata,
which comes from pyproject.toml.
"""

import importlib
import importlib.metadata
import re
from pathlib import Path
from unittest.mock import Mock

import ocpp_broker
from ocpp_broker import _version, server
from ocpp_broker.api_server import create_api
from ocpp_broker.broker import OcppBroker

SRC = Path(ocpp_broker.__file__).resolve().parent


def test_dunder_version_is_the_installed_distribution_version():
    assert ocpp_broker.__version__ == importlib.metadata.version("ocpp-broker")


def test_both_fastapi_apps_report_the_same_version():
    assert server.app.version == ocpp_broker.__version__
    assert create_api(Mock(spec=OcppBroker)).version == ocpp_broker.__version__


def test_no_other_module_hard_codes_a_version():
    literal = re.compile(r"""(__version__|\bversion)\s*=\s*["']\d""")
    offenders = [
        path.name
        for path in SRC.rglob("*.py")
        if path.name != "_version.py" and literal.search(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"version literals found in: {offenders}"


def test_falls_back_when_running_from_an_uninstalled_source_tree(monkeypatch):
    def missing(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", missing)
    try:
        reloaded = importlib.reload(_version)
        assert reloaded.__version__ == "0+unknown"
    finally:
        monkeypatch.undo()
        importlib.reload(_version)
