"""Load the hyphenated ComfyUI package explicitly for pytest root collection."""

import sys
from test_support import load_package

# Pytest otherwise imports the root __init__.py without a package because its directory has hyphens.
sys.modules.setdefault("__init__", load_package())
