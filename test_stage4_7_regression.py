from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeDB:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class Stage47RegressionTests(unittest.TestCase):
    def test_atomic_commits_once(self) -> None:
        module = load_module("transaction_utils_stage47", ROOT / "app" / "transaction_utils.py")
        db = FakeDB()
        with module.atomic(db):
            pass
        self.assertEqual(db.commits, 1)
        self.assertEqual(db.rollbacks, 0)

    def test_atomic_rolls_back_once(self) -> None:
        module = load_module("transaction_utils_stage47_error", ROOT / "app" / "transaction_utils.py")
        db = FakeDB()
        with self.assertRaises(ValueError):
            with module.atomic(db):
                raise ValueError("expected")
        self.assertEqual(db.commits, 0)
        self.assertEqual(db.rollbacks, 1)

    def test_regression_audit_helpers(self) -> None:
        module = load_module("stage47_audit", ROOT / "RUN_STAGE4_7_REGRESSION.py")
        routes = module.extract_routes()
        self.assertGreater(len(routes), 25)
        self.assertFalse(any(module.audit_sqlite_runtime_markers().values()))
        self.assertFalse(module.audit_templates()["missing"])


if __name__ == "__main__":
    unittest.main()
