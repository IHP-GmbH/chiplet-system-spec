#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
#
# The strict-live checkpoint gate, mirroring interposer-pnr/scripts/checkpoint.sh:
#
#   1. resource floor       (hard rule: >=10 GB RAM available AND >=4 cores idle;
#                            the host is shared, experiments run serially)
#   2. strict-live suite    (HCDP_STRICT_LIVE=1: a skip = a failure, except the
#                            by-design allowlist in conftest; it is empty)
#   3. skip-allowlist audit (exactly 0 allowlisted skips here) + suite floor
#                            (a passed count below the floor means tests vanished)
#
# A green claim is quoted only after this passes.
set -euo pipefail
cd "$(dirname "$0")/.."

SUITE_FLOOR=86
EXPECTED_ALLOWLISTED_SKIPS=0
PY=.venv/bin/python

echo "== resource floor =="
avail_gb=$(free -g | awk '/^Mem:/{print $7}')
if [ "${avail_gb}" -lt 10 ]; then
    echo "checkpoint: ABORT -- only ${avail_gb} GB RAM available (<10 GB floor)" >&2
    exit 1
fi
echo "${avail_gb} GB available -- OK"
# Free cores from ACTUAL cpu utilisation (/proc/stat over 1 s), not loadavg: this
# shared multi-user host reads a high loadavg while few cores are truly busy.
cores=$(nproc)
_cpu_idle_total() { awk '/^cpu /{print $5+$6, $2+$3+$4+$5+$6+$7+$8+$9}' /proc/stat; }
read -r idle0 total0 < <(_cpu_idle_total)
sleep 1
read -r idle1 total1 < <(_cpu_idle_total)
free_cores=$(awk -v i0="${idle0}" -v t0="${total0}" -v i1="${idle1}" -v t1="${total1}" \
    -v n="${cores}" 'BEGIN{d=t1-t0; if(d<=0){print n; exit} printf "%.1f", n*(i1-i0)/d}')
if awk -v f="${free_cores}" 'BEGIN{exit !(f < 4)}'; then
    echo "checkpoint: ABORT -- only ${free_cores} of ${cores} cores idle (<4 floor)" >&2
    exit 1
fi
echo "${free_cores} of ${cores} cores idle -- OK"

echo "== regenerate the emitted .chiplet artifacts (under route parity) =="
"${PY}" examples/two_die_ihp.py --out build/two_die_ihp.chiplet
"${PY}" examples/two_die_typed.py --out build/two_die_typed.chiplet

echo "== strict-live suite (serial, nice) =="
pytest_out=$(mktemp)
# -o addopts= clears the default deselection in pyproject.toml: a checkpoint
# runs every live case, and strict mode fails the ones that cannot run.
HCDP_STRICT_LIVE=1 nice -n 10 "${PY}" -m pytest -o addopts= -q -rs | tee "${pytest_out}"

echo "== skip-allowlist audit + suite floor =="
allow=$(sed -n 's/^HCDP_STRICT_LIVE allowlisted skips: \([0-9][0-9]*\)$/\1/p' "${pytest_out}")
if [ "${allow:-none}" != "${EXPECTED_ALLOWLISTED_SKIPS}" ]; then
    echo "checkpoint: ABORT -- expected exactly ${EXPECTED_ALLOWLISTED_SKIPS} allowlisted skips, saw '${allow:-none}'" >&2
    exit 1
fi
passed=$(sed -n 's/.*\b\([0-9][0-9]*\) passed.*/\1/p' "${pytest_out}" | tail -1)
if [ -z "${passed}" ] || [ "${passed}" -lt "${SUITE_FLOOR}" ]; then
    echo "checkpoint: ABORT -- suite shrank (passed=${passed:-0} < ${SUITE_FLOOR})" >&2
    exit 1
fi
echo "allowlisted skips=${allow}, passed=${passed} (floor ${SUITE_FLOOR}) -- OK"
rm -f "${pytest_out}"

echo "== checkpoint gate PASSED =="
