# Strict admission library capability

`chiplet_format_io.die_contract.STRICT_ADMISSION_API_VERSION` is the integer `4`.
It identifies the reviewed strict reader boundary, not a new document field,
schema version, physical qualification or authenticated provenance receipt.

It moves when that boundary moves, which is a new call, a new member of a table
a consumer switches on, or a different answer for the same bytes. The trigger is
the moved answer, whatever version the document declares: a verdict that moves
on a document stamped ABOVE the supported minor bumps this integer exactly as
one stamped at it does. Adopting a floor is a sufficient reason and not the only
one, because adopting a minor moves verdicts on every document stamped at it, so
a format minor this reader takes up is a consumer event even when the addition
itself is one optional key. A consumer that pins the integer exactly is
re-pinned on every such release, and that is the pin working rather than the pin
being a release counter. What never bumps it is a minor this reader does not
implement: the format defining `1.3` changes no answer here until some reader
release reads those bytes differently.

Adoption is all or nothing, and a conformance test holds it that way: no node
may be introduced above the supported minor. Without that rule this reader could
come to recognise one value of a minor it has not adopted while still refusing
the rest, and the verdict on a document at that minor would move with no version
a consumer can see. With it, a verdict is reproducible under any reader
declaring the same capability, which is the sentence a stored report needs to be
able to make.

That report needs three fields to make it: the reader release, this integer and
the document version. Two identical admissions across a re-vendor are not the
same claim, and a report naming none of the three does not say which claim it
is.

The other half of the promise, that a moved answer bumps the integer, is held
by the verdict ledger, `conformance/fixtures/die_contract_verdict_baseline.jsonl`.
A listed set of documents (`conformance/fixtures/die_contract_verdicts/`) is
asked two questions, `bind` at its defaults and then
`check_terminals(require_port_identity=True)` on what bound, and the answer is
recorded as an outcome class per question plus the `(pointer, disposition)`
pairs reported unread, keyed by the document's sha256 and this integer. Those
two questions are the ledger's, not the whole strict profile: the size,
opening, set-equality and unique-name knobs, the artwork binding of
`admit_component` and `attestation_state` are outside it, and an answer that
moves only there moves with no row to collide with. Inside them the corpus is
held to reach every outcome class and every element disposition the reader can
report, so the claim is as wide as the answer space of the two questions and no
wider. The ledger is append-only and historical: a row is written from the same
commit as the reader it describes, so it agrees with that reader by
construction, and the property enforced is that no two rows share a key. A
reader whose answer moved without a bump produces a row that collides with the
one already recorded, and the fix is the bump (a new key, the old row kept as
what the previous capability said) or the restored answer, never an edited row.
Two of the documents are pinned by policy as well as by the ledger, because no
bump may move them: a minor this reader does not implement is owed every
conforming reading it can give, for as long as it is above the floor, and a
rail with a hole has no defensible lane at any minor. A consumer that vendors
this reader re-runs the conformance of the tree it staged, which imports that
tree's own reader; agreement there is the sentence "the reader you shipped
answers as the capability it declares", and it says nothing about a reader
installed from anywhere else.

The third field earns its place on a reader older than the document, which is
the one case where the refusal text is actively misleading. A revision 1 reader
meeting a well-formed `1.1` contract does not report that it is too old; it
validates against the only schema it has and reports that the document is
wrong, naming a terminal that is correct at the minor it declares. Nothing
disagrees on the way there: a consumer pinning revision 1 gets revision 1, the
capability check passes, and the false sentence is produced downstream on schema
grounds, so a gate built to fail closed on capability mismatch never sees it.
This is measured, not argued: a `1.1` contract carrying `port` and `ordinal`
bound by a revision 1 reader is refused with `terminals/5 violates the shipped
schema: Additional properties are not allowed`.

Measured again on 2026-09-17 with a producer-emitted `1.1` document, the same
reader says something sharper. It first warns that `1.1` is newer than its
supported `1.0` and that it is reading the document as `1.0` while ignoring
unknown additions, then refuses the document for one of those additions. The
two layers state different contracts: the version policy promises a tolerance,
the shipped `1.0` schema is a closed set, and nothing downstream honours what
the warning promised. That is why a revision 1 reader cannot be repaired by
softening its message; only the element-level reading of revision 2 reconciles
the layers. One trap for anyone reproducing this: an interpreter without
`jsonschema` refuses with the same exception type and a different message
(strict admission refuses rather than degrading to a partial check), and has
measured nothing about the schema. The two refusals are told apart by their
message, never by their type.

Removing that answer is what the element-level disposition is for, and from
revision 2 on the same document is read rather than blamed. It is written down
because a vendored reader is replaced on its own schedule, so for as long as one
revision 1 reader is installed anywhere this is the answer that reader gives,
and the refusal text alone cannot tell a person that the reader predates the
document. The reader release in the report is what tells them.

Revision 1 includes full shipped-schema validation, exact inventory DBU and
identity binding, component artwork/top-cell binding, sidecar-relative paths,
and unresolved attestations unless a qualified evaluator supplies a verdict.
The existing conformance suite exercises these behaviors. A hash binding alone
does not prove an inventory was extracted from GDS or that a die is fabricable.

Revision 2 keeps every revision 1 verdict for a contract that declares `1.0`
and reads a contract that declares a higher minor element by element, as
[VERSION_POLICY.md](VERSION_POLICY.md) requires of a MINOR. The document is
checked against the shipped schema with its closed nodes opened, so bytes with
no conforming reading are still refused. What remains at each closed node has
one disposition in `DISPOSITIONS`, and a conformance test holds that table equal
to the schema's closed nodes:

- an unknown key is ignored, reported through `on_warn` and
  `BoundContract.unrecognised`, and left out of `BoundContract.terminals` and
  `resolve_endpoint`, while `BoundContract.contract` keeps the document as
  written;
- an unknown terminal `role` or `direction` refuses that terminal, which still
  counts for duplicate identity, inventory membership, repeated names, set
  equality, size and the opening rule;
- an unknown `opening.purpose` or `opening.shape` refuses that opening wherever
  an opening is read: under `require_opening`, and in `resolve_endpoint`;
- an unknown attestation key, `check` or `execution` resolves that entry to
  `unresolved` before any evaluator reads it, and so does an unknown `result`
  when `attestation_state` is given the contract's `version` and it is a newer
  minor; admission itself reads no attestation;
- an unknown `inventory.kind` leaves no inventory to read, and an unknown
  `die.frame` leaves the die box uncompared; either makes the binding
  incomplete.

Any refusal that does not need the unread element is raised first. Admission
checks every digest, the placed artwork and top cell, and every terminal check
that does not depend on the unknown element before it can report incompleteness;
with an unknown inventory kind nothing that reads the inventory (the units, the
box, membership, size, equality) can run; `bind` on its own raises
incompleteness for an unknown kind or frame after everything it checks. When
nothing is refused and some element was not read, admission raises
`DieContractIncomplete`, a `DieContractError` subclass carrying `.unrecognised`,
so a consumer that catches the parent still fails closed and one that separates
the two can report "cannot tell". A closed node with no disposition is read as
incomplete, never admitted.

One verdict moves for an attestation entry built in memory rather than read
from a contract: an entry carrying a key or check outside the schema is now
`unresolved` at any version, where revision 1 could return the evaluator's
verdict. A validated `1.0` contract cannot carry either.

Revision 3 moves the accepted minor to `1.1` and reads the optional terminal
port identity that minor adds. Two things change, and only one of them is
additive.

The additive half is the port. A terminal may carry `port` and `ordinal`
together, never one alone, and `resolve_port(bound, port, ordinal)` joins that
external identity to the physical `(name, pad_index)` the rest of the reader
works in. `pad_index` is not a substitute: moving one pad renumbers every later
one, so intent that named a pad number would name a different pad after any
pad-ring edit. `port_terminals(bound, port)` returns the whole bus in ordinal
order and is where the bus shape is checked, once: ordinals that repeat, skip a
value or start above zero describe a bus with a hole, and lane k of such a port
has no defensible answer. Resolving a lane whose pad name repeats on the die is
refused rather than answered, because what leaves the reader is a bare pad name
and the bump consumer hands out same-name pads by occurrence order.
`check_terminals(..., require_port_identity=True)` asks the same questions of
the whole die before anything is resolved, which is where a producer wants to
hear them, and adds one the per-lane call cannot ask: a signal port that covers
more than one terminal is a bus under a single name. The identity stays OPTIONAL
in the document, so a contract that carries none is refused by the profile or by
`port_terminals`, never at load time, and a consumer that resolves by pad name
is untouched.

The half that MOVES A VERDICT is the floor, and it moves more than the port. The
element-level reading of revision 2 applies to a document stamped ABOVE the minor
this reader implements, so moving the floor from `1.0` to `1.1` takes `1.1` out
of it entirely: a `1.1` document is now validated against the shipped `1.1`
schema, and every difference from it refuses the DOCUMENT. Stated as the three
classes a consumer will meet:

- an unknown KEY anywhere in a `1.1` document (top level, `die`, `process`,
  `inventory`, `build_record`, a terminal, an `opening`) was ignored, reported
  through `on_warn` and ADMITTED by revision 2, and is refused by revision 3.
  This is the largest class and the only one that turns a green admission red;
- an unknown attestation key, `check`, `execution` or `result` in a `1.1`
  document resolved that entry to `unresolved` and admitted the die under
  revision 2, and is refused by revision 3. An unknown key on an attestation
  entry belongs here rather than above: an entry is read whole or not at all;
- an unknown terminal `role` or `direction`, `opening.purpose` or
  `opening.shape`, `inventory.kind` or `die.frame` in a `1.1` document was
  reported through `DieContractIncomplete` by revision 2, and is refused by
  revision 3.

That is the `solder_bump` rule of [VERSION_POLICY.md](VERSION_POLICY.md) and not
a retreat from the element clause: no producer may emit a value before the minor
that adds it, so at a minor this reader fully implements no conforming reading is
owed. A `1.2` document is still read element by element, exactly as before, with
one pairing deliberately relaxed for it: `port` without `ordinal` refuses the
terminal's port identity rather than the document, because a later minor may
stop requiring the pair and those bytes then have a conforming reading.

Two adjacent documents therefore get opposite treatment for the same unknown
key, decided by nothing but the version they declare: refused whole at `1.1`,
read element by element at `1.2`. That follows from the policy, and it is
written here because somebody has to say it at a gate rather than derive it. At
`1.1` no producer may emit a value the minor does not define, so those bytes are
owed no conforming reading; `1.2` is a minor this reader does not implement, and
a reader owes an unimplemented minor as much reading as it can conform to.

Because the floor moved, a document is now validated against the schema AS OF
the minor it declares, reconstructed by removing every node in `INTRODUCED_AT`
introduced after that version. A `1.0` contract carrying `port` is refused for
carrying a key its own minor does not define, rather than admitted because a
later minor happens to define it. A conformance test holds the reconstructed
`1.0` schema equal to the bytes `1.0` shipped, so a future minor that edits the
schema without adding its row is caught rather than trusted.

Revision 4 moves the accepted minor to `1.2` and reads the optional top-level
`supply_domains`: one entry per supply domain the power and ground terminals
name, its nominal `volts`. The schema holds the shape (an object of
`{volts: number >= 0}`, an unknown key in an entry ignored above the floor); the
reader holds what spans keys, refusing the document when the keys are not
exactly the domains those terminals name, a ground domain is not `0`, a power
domain is not above `0`, or one domain is named by both a power and a ground
terminal. Above the floor a key no known terminal names is not refused, since a
newer minor's role may name its own domain and that terminal is refused alone.
The rules live in `chiplet_format_io.supply_domains`, which the system
description's raw contract reader shares. Absent, nothing is declared and
nothing is checked, and a `1.1` document carrying it is refused, as `1.0`
carrying `port` was.

The verdicts that move are the same three classes revision 3 moved, one minor
on: on a document stamped `1.2`, an unknown key, an unknown attestation key or
value, and an unknown role, direction, opening purpose or shape, inventory kind
or frame were read element by element by revision 3 and refuse the document
under revision 4. A `1.3` document is read element by element as `1.2` was, the
`port` without `ordinal` pairing relaxed for it. `1.0` and `1.1` documents keep
every verdict. When revision 4 shipped no producer stamped `1.2`, so the move
reached the ledger's fixtures and no die anyone had built.

Strict consumers should require a supported capability revision as well as the
`chiplet-format-io[strict]` dependency and the qualified package/source
identity. A consumer should accept exactly the revisions it was written against:
a revision 1 reader refuses a `1.1` contract as a document, which is the defect
revision 2 removes. A consumer that requires revision 1 of the reader it imports
stops working for every contract, `1.0` included, the moment that reader moves
to revision 2, so the reader and its consumers move in one window. The same
holds for 3, with one difference worth naming: a consumer may widen to `{2, 3}`
only once revision 3 exists, and widening is not free. On a `1.1` document the
two revisions disagree about every class listed above, including the one that
turns an admission into a refusal: a producer that stamps `1.1` and adds any key
this reader does not know is admitted under revision 2 and refused whole under
revision 3. A consumer that accepts both revisions is accepting both answers, so
it widens because it can live with either, never by default. Revision 4 is the
same window one minor on: the consumers that pin `{3}` widen to `{3, 4}` while
the reader move is in flight and close to `{4}` once they ship it. Earlier
development commits exposed `bind` and `admit_component` under the same package
version without the complete boundary; those callable names or a package version
alone cannot discriminate them. Missing/unsupported capability must not silently
select a weaker reader. Missing schema or jsonschema remains a tooling failure,
not successful admission. This marker does not authenticate arbitrary code that
copies it; freeze and qualify the actual distribution at execution.

Ordinary legacy readers need not import this optional strict API. The die
contract document version moves to `1.1` with revision 3 and to `1.2` with
revision 4, additively and in the die contract only; this library marker changes
no assembly or sidecar format.
