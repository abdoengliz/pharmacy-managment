from __future__ import annotations

import ast
import compileall
import json
import io
import re
import sys
import tokenize
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
APP = ROOT / "app"
TEMPLATES = APP / "templates"

FORBIDDEN_RUNTIME_PATTERNS = {
    "AUTOINCREMENT": re.compile(r"\bAUTOINCREMENT\b", re.I),
    "PRAGMA": re.compile(r"\bPRAGMA\b", re.I),
    "sqlite_master": re.compile(r"\bsqlite_master\b", re.I),
    "INSERT OR IGNORE": re.compile(r"\bINSERT\s+OR\s+IGNORE\b", re.I),
    "GROUP_CONCAT": re.compile(r"\bGROUP_CONCAT\s*\(", re.I),
    "lastrowid": re.compile(r"\.lastrowid\b", re.I),
}

CRITICAL_ROUTE_PATHS = {
    "/",
    "/login",
    "/users",
    "/suppliers",
    "/payments",
    "/revenues",
    "/expenses",
    "/external-debts",
    "/treasury-transfers",
    "/sales-invoices",
    "/stock-transfers",
    "/reports",
    "/system-health",
}


def python_files() -> list[Path]:
    return sorted(p for p in APP.rglob("*.py") if "__pycache__" not in p.parts)


def parse_python(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def audit_sqlite_runtime_markers() -> dict[str, list[str]]:
    findings: dict[str, list[str]] = {name: [] for name in FORBIDDEN_RUNTIME_PATTERNS}
    for path in python_files():
        # db_compat.py intentionally contains the isolated legacy translation layer.
        if path.name == "db_compat.py":
            continue
        raw_text = path.read_text(encoding="utf-8")
        # Remove Python comments so words such as "pragma: no cover" do not
        # become false positives in the SQL compatibility audit.
        tokens = []
        for token in tokenize.generate_tokens(io.StringIO(raw_text).readline):
            if token.type != tokenize.COMMENT:
                tokens.append(token)
        text = tokenize.untokenize(tokens)
        rel = path.relative_to(ROOT).as_posix()
        for name, pattern in FORBIDDEN_RUNTIME_PATTERNS.items():
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                findings[name].append(f"{rel}:{line}")
    return findings


def extract_routes() -> list[dict[str, Any]]:
    routes: list[dict[str, Any]] = []
    for path in python_files():
        tree = parse_python(path)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                func = decorator.func
                if not isinstance(func, ast.Attribute) or func.attr not in {"route", "get", "post", "put", "patch", "delete"}:
                    continue
                if not decorator.args or not isinstance(decorator.args[0], ast.Constant):
                    continue
                route_path = decorator.args[0].value
                if not isinstance(route_path, str):
                    continue
                methods = [func.attr.upper()] if func.attr != "route" else ["GET"]
                for kw in decorator.keywords:
                    if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                        methods = [e.value for e in kw.value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
                routes.append({
                    "path": route_path,
                    "endpoint": node.name,
                    "methods": methods,
                    "source": f"{path.relative_to(ROOT).as_posix()}:{node.lineno}",
                })
    return sorted(routes, key=lambda item: (item["path"], item["endpoint"]))


def extract_static_templates() -> dict[str, list[str]]:
    references: dict[str, list[str]] = {}
    for path in python_files():
        tree = parse_python(path)
        rel = path.relative_to(ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            if name != "render_template" or not node.args:
                continue
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                references.setdefault(arg.value, []).append(f"{rel}:{node.lineno}")
    return references


def audit_templates() -> dict[str, Any]:
    refs = extract_static_templates()
    missing = {name: sources for name, sources in refs.items() if not (TEMPLATES / name).is_file()}
    return {
        "static_references": len(refs),
        "missing": missing,
    }


def audit_security_and_transactions() -> dict[str, bool]:
    init_text = (APP / "__init__.py").read_text(encoding="utf-8")
    core_text = (APP / "core.py").read_text(encoding="utf-8")
    tx_text = (APP / "transaction_utils.py").read_text(encoding="utf-8")
    routes_text = (APP / "routes.py").read_text(encoding="utf-8")
    return {
        "csrf_protection": "def csrf_protect" in init_text,
        "secure_session_cookie_config": "SESSION_COOKIE_SECURE" in init_text,
        "http_only_session_cookie": "SESSION_COOKIE_HTTPONLY=True" in init_text,
        "security_headers": "def security_headers" in init_text,
        "production_secret_validation": "Production requires a fixed SECRET_KEY" in init_text,
        "atomic_helper": "def atomic(" in tx_text and "db.rollback()" in tx_text and "db.commit()" in tx_text,
        "request_exception_rollback": "rollback" in core_text.lower() and "teardown" in core_text.lower(),
        "audit_supports_deferred_commit": "commit: bool = True" in core_text or "commit=True" in core_text,
        "critical_routes_use_deferred_audit": "commit=False" in routes_text,
    }


def main() -> int:
    compile_ok = compileall.compile_dir(str(APP), quiet=1, force=True)
    markers = audit_sqlite_runtime_markers()
    routes = extract_routes()
    route_paths = {item["path"] for item in routes}
    missing_critical_routes = sorted(CRITICAL_ROUTE_PATHS - route_paths)
    templates = audit_templates()
    controls = audit_security_and_transactions()

    passed = (
        compile_ok
        and not any(markers.values())
        and not missing_critical_routes
        and not templates["missing"]
        and all(controls.values())
    )

    report = {
        "stage": "4.7",
        "baseline": "4.6",
        "focus": "regression and production-readiness audit",
        "result": "PASS" if passed else "REVIEW_REQUIRED",
        "validation": {
            "python_compileall": "passed" if compile_ok else "failed",
            "runtime_sqlite_markers_excluding_db_compat": {k: len(v) for k, v in markers.items()},
            "route_count": len(routes),
            "missing_critical_routes": missing_critical_routes,
            "static_template_references": templates["static_references"],
            "missing_templates": templates["missing"],
            "security_and_transaction_controls": controls,
        },
        "routes": routes,
        "limitations": [
            "This is a deterministic static regression audit and does not replace live Supabase integration tests.",
            "Financial workflows must still be exercised against a staging database before production deployment.",
        ],
    }
    output = ROOT / "POSTGRES_NATIVE_STAGE4_7_READINESS.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["validation"], ensure_ascii=False, indent=2))
    print(f"\nStage 4.7 result: {report['result']}")
    print(f"Report: {output.name}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
