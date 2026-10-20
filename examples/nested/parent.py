"""Host tool: one step that launches a child process inside it."""
import os
import subprocess
import sys
from pathlib import Path

from flattrace import Writer

w = Writer.from_env("my-addon", "1.0", argv=["my-addon"])
child = [sys.executable, str(Path(__file__).with_name("child.py"))]
if w is None:
    subprocess.run(child, check=True)
else:
    preflight = w.start("preflight")
    subprocess.run(child, env={**os.environ, **w.child_env(preflight)}, check=True)
    w.end(preflight, "ok")
    w.close()
