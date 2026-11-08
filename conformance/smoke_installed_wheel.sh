#!/usr/bin/env bash
# Build the wheel, install it into a THROWAWAY venv, and run strict admission
# from a directory that is not the checkout.
#
# The conformance suite imports from the source tree, where the schema is
# reachable because the repository is right there. That proves nothing about the
# wheel, and strict admission fails closed without the schema, so a wheel that
# ships without it stops every strict consumer. Nothing here touches a shared
# environment: the venv lives in a temporary directory and is removed.
#
# Exit codes: 0 installed package behaves, 1 it does not, 3 cannot tell (the
# environment could not be prepared, usually no network for the dependencies).
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/.." && pwd)
python=${PYTHON:-python3}

work=$(mktemp -d)
cleanup() { rm -rf "$work"; }
trap cleanup EXIT

# Build from a COPY. Building in place leaves build/ and an egg-info behind, and
# an egg-info in the source tree makes importlib.metadata report the tree's
# version instead of the installed one, which silently turned the installed-
# versus-source drift test green while the drift was still there. A smoke test
# that alters what another test measures is not a smoke test.
# Copy the REPOSITORY, not just the package: the schemas the build copies in
# live at the repository root, so a package-only copy builds a wheel with no
# schema in it. That is a real failure mode and this test caught it once already.
echo "building wheel from a pristine copy of the repository"
mkdir -p "$work/src"
tar -C "$repo" --exclude=.git --exclude=build --exclude='*.egg-info' \
    --exclude=__pycache__ --exclude=.pytest_cache -cf - . | tar -C "$work/src" -xf -
if ! "$python" -m build --wheel --outdir "$work/dist" "$work/src/reference/python" \
        > "$work/build.log" 2>&1; then
  echo "CANNOT TELL: the wheel could not be built; see below"
  tail -20 "$work/build.log"
  exit 3
fi
wheel=$(ls "$work"/dist/*.whl)
echo "built $(basename "$wheel")"

# The sdist leg. The canonical schemas live above this project directory, where
# setuptools will not reach when it packs an archive, so the sdist stages a
# generated copy and the wheel built FROM the archive has to come out with the
# schema in it just the same. Untested, that staging is a plausible-looking
# hook that nobody has watched work.
echo "building sdist, then a wheel from the sdist alone"
if ! "$python" -m build --sdist --outdir "$work/sdist" "$work/src/reference/python" \
        > "$work/sdist.log" 2>&1; then
  echo "CANNOT TELL: the sdist could not be built; see below"
  tail -20 "$work/sdist.log"
  exit 3
fi
tar -C "$work/sdist" -xf "$work"/sdist/*.tar.gz
unpacked=$(ls -d "$work"/sdist/*/ | head -1)
if ! "$python" -m build --wheel --outdir "$work/from-sdist" "$unpacked" \
        > "$work/from-sdist.log" 2>&1; then
  echo "WRONG: a wheel cannot be built from the sdist, so the archive does not"
  echo "carry what the build needs; see below"
  tail -20 "$work/from-sdist.log"
  exit 1
fi
if ! "$python" -c "
import sys, zipfile
names = zipfile.ZipFile(sys.argv[1]).namelist()
want = 'chiplet_format_io/schemas/die_contract.schema.json'
sys.exit(0 if want in names else 1)
" "$(ls "$work"/from-sdist/*.whl)"; then
  echo "WRONG: the wheel built from the sdist has no schema in it"
  exit 1
fi
echo "sdist round trip carries the schema"

# The schema has to be IN the artifact, not merely in the tree it was built from.
if ! "$python" -c "
import sys, zipfile
names = zipfile.ZipFile(sys.argv[1]).namelist()
want = 'chiplet_format_io/schemas/die_contract.schema.json'
sys.exit(0 if want in names else 1)
" "$wheel"; then
  echo "WRONG: the wheel does not carry chiplet_format_io/schemas/die_contract.schema.json"
  exit 1
fi
echo "wheel carries the schema"

"$python" -m venv "$work/venv"
if ! "$work/venv/bin/pip" install --quiet "${wheel}[strict]" > "$work/install.log" 2>&1; then
  echo "CANNOT TELL: the wheel and its strict extra could not be installed"
  tail -10 "$work/install.log"
  exit 3
fi

mkdir -p "$work/elsewhere"
cd "$work/elsewhere"

# -I, not merely a different directory. PYTHONPATH is inherited, so a run from
# elsewhere can still import the source tree and grade it as if it were the
# wheel. Isolated mode drops PYTHONPATH and the user site; the venv prefix is
# passed in so the smoke asserts where it actually imported from rather than
# trusting the invocation.
echo "installed run (isolated)"
"$work/venv/bin/python" -I "$here/installed_smoke.py" "$work/venv"
installed=$?
if [ "$installed" -ne 0 ]; then
  exit "$installed"
fi

# The counterexample. Same smoke, no isolation, PYTHONPATH aimed at the source
# tree: it must report cannot-tell rather than pass. Without this the origin
# assertion above is a line of code nobody has seen fire, and the source tree
# would pass this smoke happily, since it reaches the canonical schemas by
# being a checkout. Proving the check is reachable is the point.
echo "hostile PYTHONPATH counterexample"
set +e
PYTHONPATH="$work/src/reference/python" "$work/venv/bin/python" \
    "$here/installed_smoke.py" "$work/venv" > "$work/hostile.log" 2>&1
hostile=$?
set -e
sed 's/^/  /' "$work/hostile.log"
if [ "$hostile" -ne 3 ]; then
  echo "WRONG: the origin check did not fire under a hostile PYTHONPATH; it" \
       "returned $hostile where 3 was required, so the isolated run above" \
       "proves nothing about which package it graded"
  exit 1
fi
echo "origin check fires when it should"
