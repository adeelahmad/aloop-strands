import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))  # scripted_semver helpers

if os.environ.get("ALOOP_REQUIRE_BOX") == "1":
    from aloop_strands.box import find_box
    if not find_box():
        raise RuntimeError("ALOOP_REQUIRE_BOX=1 but no box binary: Box tests would silently skip")
