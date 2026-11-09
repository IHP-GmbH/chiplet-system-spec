"""Strict admission from an INSTALLED chiplet-format-io, run outside the checkout.

The conformance suite imports the package from the source tree, where the
schema is reachable because the repository is right there. That says nothing
about a wheel: strict admission refuses when the schema is not in the
installation, so a wheel that ships without it fails closed and every strict
consumer stops. This checks the installed artifact instead of the tree.

Exit codes, named before the run rather than after it:
    0  the installed package admitted a valid contract and refused the control
    1  the installed package is wrong: it admitted the control, or refused the
       valid contract, or cannot see its own schema
    3  cannot tell: the artifact under test was not the one imported, or the
       environment could not be prepared (no jsonschema, so strict admission is
       not exercised at all)

Usage: installed_smoke.py [expected installation prefix]

The prefix is the throwaway venv. Given it, this refuses to report anything
about a package it did not import FROM there: cwd is not proof of origin while
PYTHONPATH is inherited, and a run that silently graded the source tree would
be the same class of mistake as a wheel that silently shipped without a schema.
"""
import hashlib
import json
import os
import sys
import tempfile
import warnings
from pathlib import Path

OK, WRONG, CANNOT_TELL = 0, 1, 3


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2) + "\n")
    return Path(path)


def build_tree(root):
    layout = root / "driver.gds"
    layout.write_bytes(b"stand-in bytes; the binder hashes them, it does not read them")
    build = write(root / "build_record.json", {"template": "chiplet-full"})
    pins = write(root / "pins.json", {
        "version": "1.0", "source_type": "hand_authored", "dbu_um": 0.001,
        "die_bbox_dbu": {"x_min": 0, "y_min": 0, "x_max": 500000, "y_max": 400000},
        "pins": [{"name": "d2d_fclk", "pad_index": 0, "center_x_dbu": 1000,
                  "center_y_dbu": 2000, "width_dbu": 40000, "height_dbu": 40000}],
    })
    contract = write(root / "die_contract.json", {
        "schema": "die-output-contract", "version": "1.0",
        "die": {"name": "driver", "layout": layout.name, "layout_sha256": sha(layout),
                "top_cell": "DRIVER", "dbu_um": 0.001,
                "bbox_um": [0.0, 0.0, 500.0, 400.0], "frame": "die_local_gds_origin"},
        "process": {"pdk": "ihp-sg13g2", "pdk_version": "dev-0de0a17"},
        "build_record": {"path": build.name, "sha256": sha(build)},
        "inventory": {"kind": "pins_json", "path": pins.name, "sha256": sha(pins)},
        "terminals": [{"name": "d2d_fclk", "pad_index": 0, "role": "signal",
                       "opening": {"purpose": "aperture", "gds_layer": 9,
                                   "gds_datatype": 0, "shape": "octagon"}}],
    })
    return contract, layout


def main():
    try:
        import jsonschema  # noqa: F401
    except ImportError:
        print("CANNOT TELL: jsonschema absent, strict admission was not exercised")
        return CANNOT_TELL

    import chiplet_format_io
    from chiplet_format_io import die_contract as dc

    where = Path(chiplet_format_io.__file__).resolve().parent
    if Path.cwd() == where or where in Path.cwd().parents:
        print("CANNOT TELL: running inside the package directory, which is not the case "
              "under test")
        return CANNOT_TELL

    if len(sys.argv) > 1:
        prefix = Path(sys.argv[1]).resolve()
        if prefix != where and prefix not in where.parents:
            print("CANNOT TELL: imported %s, which is not inside the installation "
                  "under test at %s; this run graded something other than the "
                  "artifact" % (where, prefix))
            return CANNOT_TELL
        print("origin checked against %s" % prefix)
    print("installed at   %s" % where)
    print("running from   %s" % Path.cwd())

    if dc._read_packaged_schema("die_contract.schema.json") is None:
        print("WRONG: the installed package cannot see its own schema")
        return WRONG

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        contract, layout = build_tree(root)
        component = {"id": "driver", "type": "chiplet", "layout": layout.name,
                     "top_cell": "DRIVER",
                     "die_contract": {"path": contract.name, "sha256": sha(contract)}}
        try:
            dc.admit_component(component, base_dir=root)
        except Exception as exc:
            print("WRONG: a valid contract was refused by the installed package: %s" % exc)
            return WRONG
        print("positive       admitted")

        # One key a newer minor might add, chosen because this format has no
        # plausible use for it: refused as a document at the accepted minor,
        # read element by element above it, so the reader is revision 4.
        original = contract.read_text()
        body = json.loads(original)
        body["terminals"][0]["reserved_for_tests"] = 1
        for version, admits in (("1.2", False), ("1.3", True)):
            body["version"] = version
            write(contract, body)
            component["die_contract"]["sha256"] = sha(contract)
            said = []
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    dc.admit_component(component, base_dir=root, on_warn=said.append)
                admitted = True
            except dc.DieContractError:
                admitted = False
            if admitted != admits or (admits and not any("reserved_for_tests" in s for s in said)):
                print("WRONG: an unknown terminal key at %s was %s by the installed package"
                      % (version, "admitted" if admitted else "refused"))
                return WRONG
        print("newer minor    refused at 1.2, read element by element at 1.3")
        contract.write_text(original)

        body = json.loads(original)
        body["die"]["dbu_um"] = 1.0
        write(contract, body)
        component["die_contract"]["sha256"] = sha(contract)
        try:
            dc.admit_component(component, base_dir=root)
        except dc.DieContractError as exc:
            print("control        refused: %s" % str(exc)[:90])
            return OK
        print("WRONG: a contract in the wrong units was admitted by the installed package")
        return WRONG


if __name__ == "__main__":
    sys.exit(main())
