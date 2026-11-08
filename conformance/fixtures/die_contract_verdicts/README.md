# Die contract verdict documents

The documents the verdict ledger (`../die_contract_verdict_baseline.jsonl`,
held by `conformance/test_die_contract_verdict_baseline.py`) is written over.
Each is one die contract that the strict reader answers in a way the capability
integer promises to keep, and the ledger records that answer per document and
capability so a moved answer is caught rather than remembered. See "the verdict
ledger" in `docs/STRICT_ADMISSION_API.md`.

All of them share one tree: `pins.json` (the inventory), `build_record.json`,
and `driver.gds`, which is placeholder bytes with a real digest and not a
layout; nothing here reads the layout beyond hashing it.

| document | what it is | why it is here |
|---|---|---|
| `v1_0_conforming` | a `1.0` contract with no port identity | binds under every revision; the port profile, a revision 3 question, refuses it for carrying no identity |
| `v1_0_port_identity` | `1.0` carrying `port` and `ordinal` | refused for a key its own minor does not define, not admitted because a later minor does |
| `v1_0_stale_layout` | `1.0` whose `layout_sha256` names bytes the tree does not carry | bind checks the layout digest by default, and a stale one refuses |
| `v1_1_conforming` | the `1.0` conforming document restamped `1.1` with `port` and `ordinal` on every terminal, the ground rail on ordinals 0 and 1; a hand-authored fixture, not a document a producer wrote (its openings are octagons, its supply terminals carry no direction and its pad indices repeat) | binds and the port profile admits it under revision 3; the document a revision 1 reader refuses on schema grounds while naming a correct terminal, the measured sentence in `docs/STRICT_ADMISSION_API.md`, so the row is the answer a consumer compares an older vendored reader against |
| `v1_1_unknown_key` | `1.1` with a key the schema does not know | the class that turns a green admission red at the floor |
| `v1_1_unknown_terminal_role` | `1.1` with a terminal role the schema does not know | refused whole at the floor, where revision 2 reported incompleteness |
| `v1_1_unknown_attestation_result` | `1.1` with an attestation `result` outside the enum | refused whole at the floor, where revision 2 resolved the entry |
| `v1_1_port_bus_hole` | `1.1`, every pair valid, the ground rail on ordinals 0 and 2 | binds, and the port profile refuses; fixed by policy, not by the floor |
| `v1_2_unknown_key` | `1.2` with a key the schema does not know | read element by element under revision 3; refused at the floor under revision 4, and the policy pin moved to its `1.3` twin |
| `v1_2_port_without_ordinal` | `1.2` with `port` and no `ordinal` on one terminal | the pairing relaxed above the floor: the port identity is unreadable, the document reads, the profile cannot finish |
| `v1_2_unknown_terminal_role` | `1.2` with a terminal role the schema does not know | the terminal is refused and keeps its identity; the profile cannot finish |
| `v1_2_unknown_attestation_result` | `1.2` with an attestation `result` outside the enum | the entry is unresolved, the die reads and the profile admits it |
| `v1_2_unknown_opening_shape` | `1.2` with an `opening.shape` the schema does not know | the opening is refused wherever one is read, and this profile reads none |
| `v1_2_unknown_inventory_kind` | `1.2` with an `inventory.kind` the schema does not know | the binding is incomplete after every digest is checked; nothing is asked of the terminals |
| `v1_1_supply_domains` | `v1_1_conforming` with a `supply_domains` | refused for a key its own minor does not define, the `v1_0_port_identity` case one minor on |
| `v1_2_supply_domains` | `v1_1_conforming` restamped `1.2`, its ground domain declared at 0 volts | binds and the port profile admits it under revision 4 |
| `v1_2_ground_domain_at_volts` | the same with the ground domain at 1.2 volts | refused by the reader's cross-key rule, which the schema cannot hold |
| `v1_3_*` | each `v1_2_*` element-level document restamped `1.3` | the element-level answers revision 3 gave the `v1_2_*` twins, which revision 4 refuses at its floor |

Together they reach every outcome class of both questions and every element
disposition the reader can report, and the test holds the list to that.

To add a document, put it here, add its name to `CORPUS` in the test and run
the test: it fails once, printing the row to append. To take one out, delete
the file, remove its name from `CORPUS` and leave its rows; a row is the answer
to the bytes it names, whether or not the tree still carries them. Rows are
appended and never edited.
