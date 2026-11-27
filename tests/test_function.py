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


def test_price_function_uses_the_injected_search_and_refuses_bad_input(main):
    from firebase_functions import https_fn

    answer = {"shopping_results": [{"title": "10-Outlet Power Strip", "source": "Walmart", "price": "$19.99",
                                    "extracted_price": 19.99, "link": "https://walmart.example/strip"}]}
    out = main.handle_price({"lines": [{"line_id": "1", "description": "power strip"}]}, lambda p: answer)
    assert out["lines"][0]["replacement_cost"] == 19.99
    with pytest.raises(https_fn.HttpsError):
        main.handle_price({"lines": []}, lambda p: answer)
