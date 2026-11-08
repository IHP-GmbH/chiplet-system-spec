"""Ship the canonical schemas inside the package at build time.

Strict die-contract admission validates against the shipped schema and fails
closed without it, so the schema has to be IN the installed package. It must not
become a second copy kept in step by hand: the canonical documents live once, at
``schemas/`` in the repository root, and this copies them into the package while
building. A checkout that was never built reads them from that same directory,
so there is one source either way.

The root directory sits above this project, which setuptools will not reach into
when it packs an sdist, so the sdist STAGES a generated copy of the same
documents and ``build_py`` reads whichever of the two it is standing in. Both
copies are generated from the one authored source; neither is edited by hand.
"""
import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py
from setuptools.command.sdist import sdist

SCHEMAS = ("die_contract.schema.json", "pins.schema.json",
           "blackbox_padmap.schema.json", "chiplet.schema.json")
HERE = Path(__file__).resolve().parent
CANONICAL = HERE.parents[1] / "schemas"        # the authored documents
STAGED = HERE / "_canonical_schemas"           # what an sdist carries instead


def in_the_checkout():
    """True only in this repository's own layout, reference/python under a root.

    An unpacked sdist sits in a directory named after the distribution, so
    ``HERE.parents[1] / "schemas"`` there points at whatever happens to be beside
    the unpack directory. That is the same ambient-path mistake the schema loader
    was just corrected for, so the layout is identified rather than assumed.
    """
    return HERE.name == "python" and HERE.parent.name == "reference"


def schema_source():
    """Where the canonical documents are, from wherever this build is running."""
    if STAGED.is_dir():
        return STAGED
    if in_the_checkout() and CANONICAL.is_dir():
        return CANONICAL
    raise SystemExit(
        "no canonical schemas at %s or %s; the package cannot be built without "
        "them, because strict admission refuses without the shipped schema and a "
        "wheel that silently omitted it would fail closed for every strict "
        "consumer" % (CANONICAL, STAGED)
    )


def copy_schemas(target):
    target.mkdir(parents=True, exist_ok=True)
    source = schema_source()
    for name in SCHEMAS:
        origin = source / name
        if not origin.is_file():
            raise SystemExit("canonical schema missing: %s" % origin)
        shutil.copyfile(origin, target / name)


class build_py_with_schemas(build_py):
    def run(self):
        copy_schemas(Path(self.build_lib) / "chiplet_format_io" / "schemas")
        super().run()


class sdist_with_schemas(sdist):
    """Stage the canonical documents into the archive so it can build a wheel.

    An sdist packs this directory and nothing above it, so without this step the
    archive would ship without the schemas and the wheel built FROM it would
    fail in build_py, at a distance from the cause. The staged copy is generated
    here, from the same authored source the wheel copy comes from, so there is
    still one document to edit and no pair to keep in step.
    """

    def make_release_tree(self, base_dir, files):
        super().make_release_tree(base_dir, files)
        copy_schemas(Path(base_dir) / STAGED.name)


setup(cmdclass={"build_py": build_py_with_schemas,
                "sdist": sdist_with_schemas})
