"""Contract: every HTTP path the OpenAlgo React frontend calls must be a route the Flask app serves.

Static on both sides, so it runs without booting the app (importing `app` needs a live .env, DBs and a
broker plugin set). Routes come from the `@<blueprint>.route(...)` decorators plus each blueprint's
`url_prefix`, and from the restx namespaces registered under `/api/v1`; calls come from the axios
clients (`apiClient` -> /api/v1, `webClient`/`authClient` -> origin) and the `${API_BASE_URL}` fetch
sites. ponytail: a decorator whose path is built dynamically is not seen (route side), and an api/v1
call is only checked down to its namespace (the restx resource path inside it is not parsed).
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "src"

_BP = re.compile(r"""(\w+)\s*=\s*Blueprint\(\s*["'][\w.]+["'].*?(?:url_prefix\s*=\s*["']([^"']*)["'])?\s*\)""", re.S)
_ROUTE = re.compile(r"""@(\w+)\.route\(\s*["']([^"']*)["']""")
_NS = re.compile(r"""add_namespace\(\s*\w+\s*(?:,\s*path\s*=\s*["']([^"']*)["'])?""")
_AXIOS = re.compile(r"""\b(apiClient|webClient|authClient)\s*\.\s*(?:get|post|put|patch|delete)\s*(?:<[^>(]*(?:<[^>]*>[^>(]*)*>)?\(\s*(?P<q>[`"'])""")
_FETCH_BASE = re.compile(r"""\$\{API_BASE_URL\}""")


def _flask_routes() -> list[list[str]]:
    rules: list[str] = []
    for f in list((ROOT / "blueprints").glob("*.py")) + [ROOT / "app.py"]:  # app.py: bare @app.route
        text = f.read_text()
        prefixes = {}
        for m in re.finditer(r"""(\w+)\s*=\s*Blueprint\((.*?)\n?\)""", text, re.S):
            p = re.search(r"""url_prefix\s*=\s*["']([^"']*)["']""", m.group(2))
            prefixes[m.group(1)] = p.group(1) if p else ""
        for m in _ROUTE.finditer(text):
            if m.group(1) == "app":
                rules.append(m.group(2))
            elif m.group(1) in prefixes:
                rules.append(prefixes[m.group(1)].rstrip("/") + "/" + m.group(2).lstrip("/"))
    # restx namespaces: only the namespace path itself is known statically
    ns = (ROOT / "restx_api" / "__init__.py").read_text()
    rules += ["/api/v1" + (p or "") + "/<rest>" for p in _NS.findall(ns)] + ["/api/v1" + (p or "") for p in _NS.findall(ns)]
    return [r.strip('/').split('/') for r in rules]


def _literal(text: str, i: int) -> tuple[str, int]:
    q = text[i]
    j, depth, body = i + 1, 0, ""
    while True:
        c = text[j]
        if q == "`" and text.startswith("${", j):
            depth += 1
        elif depth and c == "}":
            depth -= 1
        elif not depth and c == q:
            return body, j
        body += c
        j += 1


def _norm(body: str) -> str:
    out, i = "", 0
    while i < len(body):
        if body.startswith("${", i):
            depth, j = 1, i + 2
            while depth:
                depth += {"{": 1, "}": -1}.get(body[j], 0)
                j += 1
            out += "\x00"
            i = j
        else:
            out += body[i]
            i += 1
    out = out.split("?")[0]
    return re.sub(r"(?<=[^/])\x00$", "", out).replace("\x00", "{x}")


def _match(path: str, rule: list[str]) -> bool:
    """Segment-wise: a Flask `<param>` or a frontend `${...}` matches any one segment."""
    segs = path.strip("/").split("/")
    return len(segs) == len(rule) and all(
        a == b or a == "{x}" or (b.startswith("<") and b.endswith(">")) for a, b in zip(segs, rule)
    )


def frontend_calls() -> dict[str, str]:
    """{absolute path: file} for every statically-known call."""
    calls: dict[str, str] = {}
    for f in SRC.rglob("*.ts*"):
        if f.suffix not in (".ts", ".tsx") or ".test." in f.name or "test" in f.parts:
            continue
        text = f.read_text()
        for m in _AXIOS.finditer(text):
            body, _ = _literal(text, m.end("q") - 1)
            path = _norm(body)
            if not path.startswith("/"):
                continue
            base = "/api/v1" if m.group(1) == "apiClient" else ""
            calls.setdefault(base + path, str(f.relative_to(SRC)))
        for m in re.finditer(r"""`\$\{API_BASE_URL\}""", text):
            body, _ = _literal(text, m.start())
            calls.setdefault(_norm(body[len("${API_BASE_URL}"):]), str(f.relative_to(SRC)))
    return calls


def test_extractor_sees_the_call_surface() -> None:
    assert len(frontend_calls()) > 150
    assert len(_flask_routes()) > 300  # route side not silently empty


def test_every_frontend_call_has_a_flask_route() -> None:
    rules = _flask_routes()
    missing = [
        f"{p}  ({src})"
        for p, src in sorted(frontend_calls().items())
        if p.strip("/") not in ("{x}", "api/v1", "api/v1/{x}") and not any(_match(p, r) for r in rules)  # fully dynamic
    ]
    assert not missing, "frontend calls with no Flask route:\n" + "\n".join(missing)


_ROOT_ABSOLUTE = re.compile(
    r"""(?:location\.(?:href|assign|replace)\s*=\s*|window\.open\(|fetch\(|new EventSource\()\s*(?:'/|"/|`/)(?!\$)"""
)


def test_no_root_absolute_navigation_or_fetch() -> None:
    """Behind the gateway the SPA lives under /apps/openalgo: `/login` there is a 404 (verified through
    stack/ui-gateway/Caddyfile), so every non-router URL must be built from API_BASE_URL."""
    bad = []
    for f in SRC.rglob("*.ts*"):
        if f.suffix in (".ts", ".tsx") and ".test." not in f.name and "test" not in f.parts:
            code = "\n".join(l for l in f.read_text().split("\n") if not l.lstrip().startswith("//"))
            bad += [f"{f.relative_to(SRC)}: {m.group(0)!r}" for m in _ROOT_ABSOLUTE.finditer(code)]
    assert not bad, "root-absolute URLs (use `${API_BASE_URL}/...`):\n" + "\n".join(bad)
