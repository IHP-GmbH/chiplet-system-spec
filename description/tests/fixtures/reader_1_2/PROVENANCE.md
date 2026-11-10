# The format 1.2 description reader

`src/` is the `description/src` tree of this repository as it stood on the last
commit before format 1.3 (the unplaced source) was added: a reader that
implements format 1.2 and knows no `unplaced` root key. It is kept here so
`tests/test_source_unplaced.py` can run that reader against a 1.3 file without
reaching into git history.

It is a byte copy except for two comments, reworded without changing any code:
the `DuplicateLane` docstring in `errors.py` and the repeated-lane comment in
`model.py`. `SHA256SUMS` lists every file of the copy, and the test refuses to
run the reader if a file differs from it or is added or missing.

Each line of `SHA256SUMS` is a sha256 digest, two spaces and a path relative to
this directory; `__pycache__` is left out. After a deliberate edit to the copy,
regenerate it from this directory with

    find src -name __pycache__ -prune -o -type f -print | LC_ALL=C sort | xargs sha256sum > SHA256SUMS

Never refresh it from a newer tree: its point is that it is the 1.2 reader.
