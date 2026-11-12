"""Copy pol/ into functions/pol/ so the Cloud Function deploys the tested code.

Runs automatically as the predeploy step in firebase.json. Works the same on
Windows and Linux. functions/pol/ is gitignored: pol/ is the only source.
"""

import shutil
from pathlib import Path

root = Path(__file__).resolve().parent.parent
src, dst = root / "pol", root / "functions" / "pol"
if dst.exists():
    shutil.rmtree(dst)
shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
print(f"copied {src} -> {dst}")
