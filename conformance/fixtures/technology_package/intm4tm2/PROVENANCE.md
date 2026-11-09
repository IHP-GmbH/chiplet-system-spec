# Provenance

The files listed under `copied` in `provenance.json` are copied byte for byte
from https://github.com/IHP-GmbH/OpenIntM4TM2, release 0.4.0 (the manifest's
`revision`), at the same paths. `provenance.json` records the sha256 of each,
which is what binds the copy: a release's files are identified by those
digests, and `conformance/test_technology_package_intm4tm2.py` holds the copy
to them. The files under `placeholders` are empty and were written here.
