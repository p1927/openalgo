"""WSGI middleware that prefixes/strips ``/apps/openalgo`` for the OpenAlgo SPA.

When the gateway (Caddy) reverse-proxies ``/apps/openalgo/*`` to the OpenAlgo
Flask app, the SPA bundle emits absolute asset paths like
``/apps/openalgo/assets/index-...js`` (because Vite's ``base: '/apps/openalgo/'``).
The browser resolves them against the embedding origin, so Flask sees
``/apps/openalgo/assets/index-...js`` in the request. Without this middleware,
every blueprint registered at root (``/``, ``/assets/...``, ``/auth/...``) would
404 — the user would log in successfully but the SPA bundle wouldn't load.

The middleware rewrites ``PATH_INFO`` so Flask's routing sees the path without
the ``/apps/openalgo`` prefix, then re-adds the prefix to outbound ``Location``
and ``Link`` headers so redirects / asset URLs come back as the same prefixed
form the browser asked for. Response bodies are not modified — the SPA's HTML
already carries the prefix (because Vite's ``base`` controls that), and the
API serves JSON that is fully relative.

Why strip on the Flask side rather than on the gateway (Caddy)?

Caddy already does ``uri strip_prefix /apps/openalgo`` for the gateway path —
that lets ``http://127.0.0.1:8080/apps/openalgo/strategy/builder`` work today.
But the same SPA also runs inside the shell's iframe pointed at
``http://127.0.0.1:5001/...``. With Caddy doing the strip, the iframe URL has
to be ``http://127.0.0.1:5001/strategy/builder`` (no prefix). Doing the strip
on the Flask side means both URLs route through the same code path:
``http://127.0.0.1:5001/apps/openalgo/strategy/builder`` and
``http://127.0.0.1:8080/apps/openalgo/strategy/builder`` both work, and either
gateway can be reconfigured in the future without rewriting Flask. Caddy stops
doing ``uri strip_prefix`` once this lands; the prefix is purely a Flask
concern.

The prefix is environment-controlled via ``OPENALGO_BASE_PREFIX`` so other
deployments (Docker at ``/openalgo``, dev tunnelling under ``/trade/...`` etc.)
can override without code changes.
"""
from __future__ import annotations

import os


# Read once at import time. Defaults to the path the stack/ui-gateway/Caddyfile
# reverse-proxies the openalgo app under. Empty string disables the middleware
# (used in single-instance dev / Docker-internal setups where the gateway is a
# sibling container that already strips the prefix itself).
BASE_PREFIX = os.environ.get("OPENALGO_BASE_PREFIX", "/apps/openalgo").rstrip("/")
BASE_PREFIX_SLASH = BASE_PREFIX + "/"


def strip_prefix_middleware(wsgi_app):
    """WSGI middleware: strip ``/apps/openalgo`` from PATH_INFO before Flask routes,
    re-add it to ``Location`` / ``Link`` headers in the response so any
    absolute redirect URLs come back as the prefixed form the browser asked
    for.

    Non-matches pass through untouched so this middleware is safe to enable
    unconditionally — direct hits on ``/`` or ``/strategy/builder`` at
    ``127.0.0.1:5001`` keep working exactly as they did before.
    """

    if not BASE_PREFIX:
        return wsgi_app

    def application(environ, start_response):
        path_info = environ.get("PATH_INFO", "") or "/"
        # Match the prefix as a leading path segment so a malicious request
        # for /apps-openalgo-asset-foo can't be misinterpreted as a prefixed
        # path (the slash between prefix and remainder is required). The
        # trailing slash is the normal SPA entry; if a request carries
        # exactly the prefix with no remainder, route it through as ``/``.
        if path_info == BASE_PREFIX or path_info == BASE_PREFIX_SLASH:
            new_path = "/"
        elif path_info.startswith(BASE_PREFIX_SLASH):
            remainder = path_info[len(BASE_PREFIX):]
            new_path = remainder if remainder.startswith("/") else "/" + remainder
        else:
            return wsgi_app(environ, start_response)

        # PATH_INFO rewrite must happen BEFORE Flask's URL map binds. We
        # also rewrite SCRIPT_NAME to the prefix so reverse_url() / url_for()
        # produce the prefixed form, and REQUEST_URI (some WSGI helpers
        # check this) for completeness. Use the original environ.copy() —
        # never mutate the caller's environ.
        new_environ = {**environ, "PATH_INFO": new_path,
                        "SCRIPT_NAME": BASE_PREFIX,
                        "REQUEST_URI": environ.get("REQUEST_URI", path_info)}

        def start_response_wrapper(status, headers, exc_info=None):
            # Re-prefix Location / Link headers so the browser sees the same
            # absolute path it asked for. The check is value-based, not
            # key-based, because WSGI headers are bytes-of-strings tuples and
            # the case-insensitive header key in the spec is honored by the
            # underlying server before our wrapper runs.
            new_headers = []
            for name, value in headers:
                lname = name.lower()
                if lname == "location" and isinstance(value, str) and value.startswith("/") and not value.startswith("//"):
                    if value == "/":
                        new_headers.append((name, BASE_PREFIX_SLASH))
                    elif BASE_PREFIX and not value.startswith(BASE_PREFIX + "/") and not value.startswith(BASE_PREFIX + "?"):
                        prefix = BASE_PREFIX + (value if value.startswith("/") else "/" + value)
                        new_headers.append((name, prefix))
                    else:
                        new_headers.append((name, value))
                elif lname == "link" and isinstance(value, str):
                    new_headers.append((name, _prefix_link_header(value)))
                else:
                    new_headers.append((name, value))
            return start_response(status, new_headers, exc_info)

        return wsgi_app(new_environ, start_response_wrapper)

    return application


def _prefix_link_header(value: str) -> str:
    """Re-prefix <link rel="..." href="..."> in HTTP Link headers.

    Flask may emit ``Link: <...>; rel=preload`` for modulepreload — browsers
    honor these for early resource hints, so they have to remain prefixed
    even though the request path was stripped.
    """
    out = []
    i = 0
    while i < len(value):
        if value[i] == "<":
            j = value.find(">", i + 1)
            if j == -1:
                break
            url = value[i + 1 : j]
            out.append("<")
            out.append(_prefix_url(url))
            out.append(">")
            i = j + 1
        else:
            out.append(value[i])
            i += 1
    return "".join(out)


def _prefix_url(url: str) -> str:
    """Add BASE_PREFIX to a single relative URL string. Skip absolute URLs,
    fragment-only refs, and already-prefixed URLs.
    """
    if not url or not BASE_PREFIX:
        return url
    if url.startswith("//") or "://" in url[:8]:
        return url
    if url.startswith(BASE_PREFIX + "/") or url.startswith(BASE_PREFIX + "?"):
        return url
    if url.startswith("/"):
        return BASE_PREFIX + url
    return url
