"""The Cloud Function body, without a live Firebase request."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
pytest.importorskip("firebase_functions")


@pytest.fixture(scope="module")
def main():
    subprocess.run([sys.executable, str(ROOT / "scripts" / "sync_functions.py")], check=True)
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "demo-pol")
    sys.path.insert(0, str(ROOT / "functions"))
    import main as m
    return m


def test_function_returns_the_checked_claim(main):
    from test_service import base_payload
    out = main.handle(base_payload())
    assert out["counts"]["consistent"] == 1


def test_bad_request_is_invalid_argument(main):
    from firebase_functions import https_fn
    with pytest.raises(https_fn.HttpsError) as e:
        main.handle({"schedule": []})
    assert e.value.code == https_fn.FunctionsErrorCode.INVALID_ARGUMENT


def test_app_check_is_enforced():
    src = (ROOT / "functions" / "main.py").read_text()
    assert "enforce_app_check=True" in src
