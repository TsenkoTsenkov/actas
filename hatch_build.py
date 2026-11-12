"""Wheel build hook: bin/actas -> actas_cli.py with BUILD = "package".

bin/actas is the only source. The wheel ships a copy as the module
`actas_cli` (entry points `actas = actas_cli:main` and the deprecated
pre-rename alias `ccenv = actas_cli:main`) in which exactly one line
differs: `BUILD = "script"` becomes `BUILD = "package"` (SPEC 5.4), so the
code can tell a package install from the single-file install.
"""

import os
import re
import shutil
import tempfile

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

BUILD_LINE = re.compile(r'^BUILD = "script"', re.MULTILINE)


def package_source(text):
    out, n = BUILD_LINE.subn('BUILD = "package"', text)
    if n != 1:
        raise RuntimeError('bin/actas must contain exactly one line starting with BUILD = "script"')
    return out


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        with open(os.path.join(self.root, "bin", "actas"), encoding="utf-8") as f:
            text = package_source(f.read())
        self._tmp = tempfile.mkdtemp(prefix="actas-build-")
        module = os.path.join(self._tmp, "actas_cli.py")
        with open(module, "w", encoding="utf-8") as f:
            f.write(text)
        build_data["force_include"][module] = "actas_cli.py"

    def finalize(self, version, build_data, artifact_path):
        shutil.rmtree(getattr(self, "_tmp", ""), ignore_errors=True)
