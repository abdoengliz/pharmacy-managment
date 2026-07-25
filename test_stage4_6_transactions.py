import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "transaction_utils", Path(__file__).parent / "app" / "transaction_utils.py"
)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)
atomic = module.atomic


class FakeDB:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_atomic_commits_once_on_success():
    db = FakeDB()
    with atomic(db):
        pass
    assert db.commits == 1
    assert db.rollbacks == 0


def test_atomic_rolls_back_on_error():
    db = FakeDB()
    try:
        with atomic(db):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert db.commits == 0
    assert db.rollbacks == 1
