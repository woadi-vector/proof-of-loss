"""Firebase Cloud Function: the cloud half of the hybrid check.

The phone (Gemini Nano through Firebase AI Logic) and cloud Gemini do the
looking; this function applies the rules in pol/service.py and returns the
checked, priced claim. It calls no model.

App Check is enforced: a request without a valid App Check token from the
registered app is refused before this code runs. During development, the app
uses a debug token registered in the Firebase console.

pol/ is copied in by scripts/sync_functions.py before each deploy (see the
predeploy step in firebase.json), so the deployed code is the tested code.
"""

from firebase_admin import initialize_app
from firebase_functions import https_fn, options

import os

from pol.comps import serpapi_get
from pol.price_service import PriceInputError, price_lines as run_pricing
from pol.service import InputError, check_claim as run_check

initialize_app()


def handle(data) -> dict:
    """The function body, kept apart from the decorator so tests can call it."""
    try:
        return run_check(data)
    except InputError as e:
        raise https_fn.HttpsError(https_fn.FunctionsErrorCode.INVALID_ARGUMENT, str(e))


@https_fn.on_call(enforce_app_check=True, invoker="public", region="us-central1",
                  memory=options.MemoryOption.MB_256, timeout_sec=60)
def check_claim(req: https_fn.CallableRequest) -> dict:
    return handle(req.data)


def handle_price(data, get_json=None) -> dict:
    """Replacement cost from cited listings. `get_json` is injected in tests."""
    try:
        return run_pricing(data, get_json or serpapi_get(os.environ.get("SERPAPI_KEY", "")))
    except PriceInputError as e:
        raise https_fn.HttpsError(https_fn.FunctionsErrorCode.INVALID_ARGUMENT, str(e))


# invoker="public": callable functions must accept the browser's request at the network
# level; App Check (enforced above) is what refuses calls that do not come from the
# registered site or phone. Set here, never edited in the Cloud Run console, because a
# console edit makes a new revision that needs a container image the cleanup policy may
# already have deleted.
# The SerpApi key lives in Secret Manager (firebase functions:secrets:set SERPAPI_KEY),
# never in the repo. One Google Shopping search per line.
@https_fn.on_call(enforce_app_check=True, invoker="public", region="us-central1", secrets=["SERPAPI_KEY"],
                  memory=options.MemoryOption.MB_256, timeout_sec=300)
def price_lines(req: https_fn.CallableRequest) -> dict:
    return handle_price(req.data)
