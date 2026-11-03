import os
from pathlib import Path

Path("build_version.py").write_text("VERSION = " + repr(os.environ["GITHUB_REF_NAME"]) + "\n")
