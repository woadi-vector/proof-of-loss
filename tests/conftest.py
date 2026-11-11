import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# test_pipeline.py is run as a script (python tests/test_pipeline.py); its test
# functions take arguments pytest would read as fixtures, so pytest skips it.
collect_ignore = ["test_pipeline.py"]
