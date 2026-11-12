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

from pol.service import InputError, check_claim as run_check

initialize_app()


def handle(data) -> dict:
    """The function body, kept apart from the decorator so tests can call it."""
    try:
        return run_check(data)
    except InputError as e:
        raise https_fn.HttpsError(https_fn.FunctionsErrorCode.INVALID_ARGUMENT, str(e))


@https_fn.on_call(enforce_app_check=True, region="us-central1",
                  memory=options.MemoryOption.MB_256, timeout_sec=60)
def check_claim(req: https_fn.CallableRequest) -> dict:
    return handle(req.data)
