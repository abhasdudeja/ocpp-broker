"""
Serves the bundled web console (the built single-page app) under ``/ui``.

The build output lives in ``ocpp_broker/ui_dist`` (``npm run build`` in ``ui/``
writes it there; it is not committed). The routes are always registered because
the FastAPI app is created before the configuration is read; ``ui.enabled`` is
checked on every request instead, and a disabled UI answers 404.

The files are public static assets: the API key never touches these routes.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

UI_PREFIX = "/ui"
DIST_DIR = Path(__file__).resolve().parent / "ui_dist"

# Windows maps these through the registry, which can yield text/plain; with
# ``nosniff`` set that would stop the browser from running the app.
for _suffix, _type in {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".svg": "image/svg+xml",
    ".json": "application/json",
    ".map": "application/json",
    ".woff2": "font/woff2",
}.items():
    mimetypes.add_type(_type, _suffix)

# No inline script or style, nothing from other origins. The API key lives in
# the page's JavaScript, so this is the main defence against it being read.
CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'none'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'none'",
    ]
)

SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
}

NOT_BUILT_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>OCPP Broker console not built</title></head>
<body style="font-family:sans-serif;max-width:40em;margin:3em auto;padding:0 1em">
<h1>The web console has not been built</h1>
<p>This installation does not include the console files.
A release package of <code>ocpp-broker</code> ships them; a source checkout needs
<code>npm ci &amp;&amp; npm run build</code> in the <code>ui/</code> directory first.</p>
<p>The REST API at <a href="/docs">/docs</a> is not affected.</p>
</body></html>
"""


def ui_enabled(broker: Any) -> bool:
    """``ui.enabled`` (default true); true too before the configuration is loaded."""
    section = (getattr(broker, "config_data", None) or {}).get("ui")
    if not isinstance(section, dict):
        return True
    return bool(section.get("enabled", True))


def ui_built() -> bool:
    return (DIST_DIR / "index.html").is_file()


def _secure(response: Response, cache: str) -> Response:
    for name, value in SECURITY_HEADERS.items():
        response.headers[name] = value
    response.headers["Cache-Control"] = cache
    return response


def _resolve(relative: str) -> Path | None:
    """The file under ``DIST_DIR`` for a URL path, or None (missing, a directory, or an escape)."""
    root = DIST_DIR.resolve()
    try:
        candidate = (root / relative).resolve()
    except (OSError, ValueError):  # e.g. an embedded NUL on some platforms
        return None
    if root not in candidate.parents or not candidate.is_file():
        return None
    return candidate


def create_ui_router(broker: Any) -> APIRouter:
    router = APIRouter(include_in_schema=False)

    def disabled() -> Response:
        return _secure(Response("Not Found", status_code=404, media_type="text/plain"), "no-store")

    def index() -> Response:
        if not ui_built():
            return _secure(HTMLResponse(NOT_BUILT_PAGE, status_code=503), "no-store")
        # index.html names the hashed assets, so it must always be revalidated
        return _secure(FileResponse(DIST_DIR / "index.html", media_type="text/html"), "no-store")

    @router.get(UI_PREFIX)
    async def ui_root() -> Response:
        if not ui_enabled(broker):
            return disabled()
        # Relative asset URLs only resolve against a trailing slash.
        return _secure(RedirectResponse(f"{UI_PREFIX}/", status_code=307), "no-store")

    @router.get(UI_PREFIX + "/{path:path}")
    async def ui_file(path: str) -> Response:
        if not ui_enabled(broker):
            return disabled()
        if not path:
            return index()
        found = _resolve(path)
        if found is not None:
            # Vite names everything under assets/ by content hash
            immutable = path.startswith("assets/")
            return _secure(
                FileResponse(found),
                "public, max-age=31536000, immutable" if immutable else "no-cache",
            )
        if Path(path).suffix:
            # A missing script or image must be a 404, not the app shell
            return _secure(Response("Not Found", status_code=404, media_type="text/plain"), "no-store")
        return index()  # client-side route such as /ui/chargers/orgA/CHG001

    return router
