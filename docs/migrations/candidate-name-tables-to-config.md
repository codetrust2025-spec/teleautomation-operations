# Moving the candidate name tables out of the code

`features/candidate_store.py` and `features/handler_expenses.py` branch on real
people's names. They are the last personal data in the repository after the
fixture cleanup, and unlike a fixture they cannot simply be renamed: each one
decides something about live rows.

This is the design for moving them into the Operations data layer. **It has not
been carried out.** It is written down so the decision to do it — or not — is
made against the real inventory and a real rollback, rather than against a
guess.

## What is actually in there

| Where | What it holds | What it decides |
| --- | --- | --- |
| `_CANDIDATE_NAME_ALIASES` + the inline rules in `canonical_candidate_name` | name variants → one display name | whether two rows are one person; drives merging, the roster, pending works |
| `_CANDIDATE_SEARCH_HINTS` | 18 nicknames → name fragments | free-text search on the candidates page |
| `is_free_service_candidate` | two first names | whether a client payment is expected at all |
| `is_low_priority_slot_booker` | three names | who can take a slot another candidate holds |
| the Tool/Data-Analyst defaults near `_persist_tool_attendee` | two names | attendee and technology written onto slots |
| `PROFILE_CLOSURE_ADMIN_REFERENCE` | one referrer name | earnings scope and the closure bonus, in five places |
| `HANDLER_PAYOUT_EXCLUDED_REF_KEYS` | one reference key | which reference is excluded from handler payout |
| `handler_expenses` spelling normaliser | one name in three casings | which expense rows belong to which referrer |

Every one is a *rule about a person*, not a label: renaming them in place would
change what production does to that person's rows.

## Why the obvious version is unsafe

Lift the tables into `<OPERATIONS_DATA_DIR>/candidate_naming.json`, import them
instead, ship. If the file is missing or misspelt on the day of the deploy,
nothing errors: `canonical_candidate_name` starts returning the raw name,
merging stops, one person becomes two rows on the roster, a complimentary
candidate starts being chased for payment, and a referrer's earnings scope
quietly changes. The failure is silent and it is about money.

## The design

**Phase 1 — read from the file, keep the table as the fallback.**

A new `features/candidate_naming_registry.py` loads the JSON from
`CANDIDATE_NAMING_REGISTRY_FILE`, or `<OPERATIONS_DATA_DIR>/candidate_naming.json`,
with the same shape as the table above. `candidate_store` asks the registry
first and falls back to the embedded table, and the registry reports which
source answered. `/version` (or the admin health payload) carries
`naming_registry: "file" | "embedded"`.

Nothing about behaviour changes in phase 1, because the file is deployed with
exactly the current values. Rollback is redeploying the previous image.

**Phase 2 — remove the embedded table.**

Only once the health signal has read `"file"` across a restart, and the file is
in the volume backup. After this the fallback is gone, so a missing file must
fail loudly: the loader raises at import rather than returning empty. A store
that cannot say who a candidate is must not serve a roster that quietly says
they are two people.

## How equivalence is proved, without publishing the names

A golden test that is a digest table, not a name list:

```python
# tests/test_candidate_naming_equivalence.py
for digest, expected in GOLDEN.items():          # sha256(name) -> sha256(canonical)
    assert sha256(canonical_candidate_name(NAMES[digest])) == expected
```

`GOLDEN` is generated once, inside the production container, over every
distinct `name` in `candidates_store` — the real input set, which is the only
set that matters — and committed as digests. `NAMES` is supplied at run time
from the same place the registry is. In CI, where neither exists, the test
skips loudly the way the Postgres tests do; on the host it is a one-command
check that phase 2 changed nothing.

Add to it the three functions that decide money —
`is_free_service_candidate`, the payout exclusion, the closure-bonus scope —
compared before and after over the same input set.

## What must not move

Stored rows. Every candidate row already holds its canonical display name as
data; the registry decides what *new* writes canonicalise to, not what old rows
say. The migration touches no row, so historical references, earnings history
and audit rows keep meaning exactly what they meant.

## Rollback

| Phase | Failure | Recovery |
| --- | --- | --- |
| 1 | file absent or malformed | fallback answers; health says `embedded`; fix the file |
| 1 | wrong values in the file | redeploy previous image, or correct the file — no row was written differently unless a name was canonicalised during the window |
| 2 | file absent | the service refuses to start; restore from the volume backup and restart |
| 2 | wrong values | restore the file; re-run the equivalence check before serving |

The window where a wrong file could write wrong data is the time between the
deploy and the check. Keep it short by running the equivalence check against
the running container immediately after each phase.

## Cost and benefit, plainly

The benefit is that the repository stops naming real candidates and referrers
in code. The cost is a two-phase deploy, a golden fixture that has to be
regenerated whenever the real set of names changes, and a new way for the
service to fail at start-up.

Nobody outside the team can act on these names: they are first names attached
to no contact detail, in a file that also explains what they are. The fixture
cleanup removed the addresses, phone numbers and meeting links that made the
old data a dossier. So this is worth doing when the naming tables are next
touched for a functional reason — not as a security emergency.
