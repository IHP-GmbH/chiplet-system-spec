# Synthetic carrier package

A minimal carrier technology package used as a test fixture. Its layer names,
GDS pairs, rules and values are invented: it describes no process, it is not
fabricable, and none of its numbers come from a real technology.

The files are validated by `conformance/test_technology_package.py`, not run:
the DRC deck, the runner and the KiCad template are placeholders that give the
package the shape a real one has. The package exists so that a second carrier
goes through the same validator as any other, and so that nothing it exercises
needs a name of the reference interposer.
