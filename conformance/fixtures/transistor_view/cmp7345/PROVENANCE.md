# Provenance

`CMP7345.cdl` and `CMP7345-final.spice` are vendored from
https://github.com/KrzysztofHerman/IHP__CMP7345 at commit
688ee1cfb81b82e6058699e429825682b25d3002, with the path comment lines removed.

`CMP7345.amended.cdl` is that CDL patched on purpose: four Xschem path comments are removed, and
eleven cards expose the body nets `sub!` and `nw` (three `.subckt` pin lists, three `x` cards and
five bulk terminals). Its header states the change.

`amendment_receipt.json` is the `cdl-reference-amendment/1` receipt of that patch. It names the
shipped file by its upstream commit, path and sha256, and the amended file by its path in this
repository and its sha256. The agreement reads the amended CDL with it and names it by its own
sha256, which `conformance/test_agreement.py` pins.
