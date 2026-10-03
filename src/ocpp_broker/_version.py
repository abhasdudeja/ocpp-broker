"""Single source of truth for the package version: the installed distribution metadata (pyproject.toml)."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("ocpp-broker")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0+unknown"
