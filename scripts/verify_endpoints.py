#!/usr/bin/env python3
"""Check every endpoint this server calls against the API document your site publishes.

A test suite that mocks the HTTP client cannot tell whether an endpoint exists:
a call to a path CheckMK does not serve looks exactly like one that works. This
reads the OpenAPI document straight from the configured site and compares it
with every `self.client.<verb>("…")` call in handlers/.

    python scripts/verify_endpoints.py                  # fetch from the site in .env
    python scripts/verify_endpoints.py --spec spec.yaml # use a document you already have

Exits non-zero when a call has no matching path and verb, so it can run before a
push. Requires PyYAML; everything else is standard library.
"""

import argparse
import ast
import base64
import re
import ssl
import sys
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

VERBS = ("get", "post", "put", "delete", "patch")
HANDLER_DIR = Path("handlers")


def fetch_spec() -> str:
    """Read the OpenAPI document from the site named in the environment."""
    sys.path.insert(0, str(Path.cwd()))
    from api import CheckMKClient  # noqa: PLC0415  -- optional, only needed without --spec
    from config import CheckMKConfig  # noqa: PLC0415
    from config.dotenv import load_dotenv  # noqa: PLC0415

    load_dotenv()
    config = CheckMKConfig.from_env()
    url = CheckMKClient(config).api_base_url.rstrip("/") + "/openapi-doc.yaml"

    request = urllib.request.Request(url)
    token = base64.b64encode(f"{config.username}:{config.password}".encode()).decode()
    request.add_header("Authorization", f"Basic {token}")

    context = ssl.create_default_context()
    if not config.verify_ssl:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    with urllib.request.urlopen(request, context=context, timeout=90) as response:
        return str(response.read().decode())


def literal(node: ast.AST, scope: Dict[str, str]) -> Optional[str]:
    """Resolve an endpoint argument to a string, following names in scope.

    Names are resolved per enclosing function, not per module: several handlers
    assign a local called `endpoint`, and treating those as one value reports
    calls against whichever assignment came last.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return scope.get(node.id)
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts.append(str(value.value))
            elif isinstance(value, ast.FormattedValue):
                parts.append(literal(value.value, scope) or "{X}")
        return "".join(parts)
    return None


def collect_calls() -> Tuple[List[Tuple[str, str, str]], List[str]]:
    calls: List[Tuple[str, str, str]] = []
    unresolved: List[str] = []

    for path in sorted(HANDLER_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        module_scope = {
            node.targets[0].id: node.value.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        }

        for function in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            scope = dict(module_scope)
            for node in ast.walk(function):
                if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                    resolved = literal(node.value, scope)
                    if resolved:
                        scope[node.targets[0].id] = resolved

                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                    continue
                if node.func.attr not in VERBS:
                    continue
                if not (isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "client"):
                    continue

                endpoint = literal(node.args[0], scope) if node.args else None
                where = f"{path}:{node.lineno}"
                if not endpoint or endpoint.startswith("{"):
                    unresolved.append(where)
                else:
                    calls.append((node.func.attr.upper(), endpoint, where))

    return calls, unresolved


def check(spec_text: str) -> int:
    import yaml  # noqa: PLC0415  -- the one third-party import, kept out of the package

    paths = yaml.safe_load(spec_text)["paths"]
    matchers = [(re.compile("^" + re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(path)) + "$"), path) for path in paths]

    calls, unresolved = collect_calls()
    problems: List[str] = []

    for verb, endpoint, where in calls:
        candidate = "/" + re.sub(r"\{[^}]+\}", "X", endpoint).lstrip("/")
        # Every matching path, not just the first: a literal path otherwise
        # hides the parameterised one it shares a prefix with.
        hits = [path for pattern, path in matchers if pattern.match(candidate)]
        if not hits:
            problems.append(f"{where}\n    {verb} {endpoint}\n    no such path in this version")
        elif not any(verb.lower() in paths[path] for path in hits):
            allowed = sorted({v.upper() for path in hits for v in paths[path] if v in VERBS})
            problems.append(f"{where}\n    {verb} {endpoint}\n    path exists, but only {', '.join(allowed)}")

    print(f"{len(calls)} endpoint calls checked, {len(unresolved)} not statically resolvable")
    if problems:
        print(f"\n{len(problems)} do not match this site's API:\n")
        for problem in problems:
            print(f"  {problem}\n")
        return 1

    print("all of them match a path and a verb this site serves")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--spec", help="an OpenAPI document to use instead of fetching one")
    args = parser.parse_args(argv)

    if args.spec:
        spec_text = Path(args.spec).read_text(encoding="utf-8")
    else:
        try:
            spec_text = fetch_spec()
        except Exception as error:  # any failure to read the document is the same failure
            print(f"could not read the API document from the configured site: {error}", file=sys.stderr)
            print("set CHECKMK_* in the environment or pass --spec", file=sys.stderr)
            return 2

    return check(spec_text)


if __name__ == "__main__":
    raise SystemExit(main())
