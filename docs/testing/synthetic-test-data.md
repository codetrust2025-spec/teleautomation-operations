# Test data is synthetic

This repository is public. Anything in a fixture is readable by anyone who
clones it, for as long as the clone exists.

The fixtures used to come straight from production, because that is where the
awkward cases are: a wrapped Teams URL, an OCR'd resume, a receipt with a masked
payee. What came with them was a candidate's Gmail address, a recruiter's mobile
number, a meeting's tenant and organiser ids — a name, a phone number and an
interview time for people who never agreed to any of it.

Keep the awkward shape. Replace the person.

## The rules

| Thing | Use | Never |
| --- | --- | --- |
| Email | `someone@example.com`, `.invalid`, `.test` | `@gmail.com` or any consumer provider |
| Work email | a pseudonym at the real employer domain — `swati.sharma@winwire.com` | a real person's work address |
| Mobile | `90000xxxxx` | a number anyone could dial |
| UPI handle | `testpayee@examplebank`, or a mask like `XXXXXX4573@ybl` | a handle that could receive money |
| Teams / Meet | `00000000-1111-4222-8333-444444444444`, `meet.google.com/tst-fake-mtg` | a join URL from a real invite |
| Calendar id | `syntheticuid000000000001ab@google.com` | the id Google generated |
| Name | a pseudonym | a real candidate's or recruiter's name |

`example.com`, `.invalid` and `.test` are reserved by RFC 2606 and RFC 6761:
nobody can register them, so a fixture address can never reach a real inbox.

## What stays real, and why

**Vendor and job-board domains.** `noreply@naukri.com`, `offers@bankbazaar.com`
and the rest are role addresses at companies, not personal data, and the domain
is the thing under test — the noise rules key on it. Replace the local part if
it names a person; leave the domain alone.

**The company's own payee identity.** Its UPI handle, the handle derived from
its registered phone, and the near-misses that prove the matching is not
forgiving. The same values are the payee configuration in
`features/payment_verification_engine.py`; a pseudonym in the test would only
make the test stop describing the rule it is named after.

**Names that production logic keys on.** `features/candidate_store.py`
canonicalises a few real names and `features/handler_expenses.py` knows one
referrer by name. A test that renames them stops testing the rule. These are
listed as a known exposure in the security audit, with moving the tables into
the operations data directory as the fix — that is a data migration, not a
fixture edit.

## The guard

`tests/test_fixtures_hold_no_personal_data.py` enforces the table above on every
file under `tests/` and every `*.test.jsx` / `*.test.js` / `__fixtures__/` file
in the dashboard. It runs in the `python` CI job, so a pull request that adds a
real address, a dialable number, a live payment handle, a real meeting, or a
name that was removed once already, fails before review.

A value that genuinely has to be real goes in `ALLOWED` in that file, with the
reason written next to it. There are five entries today; each one is a sentence
long. If a sixth is hard to justify in a sentence, it probably should not be in
a public repository.

Names removed as personal data are pinned as sha256 digests rather than as a
list — writing the list out would put back exactly what the guard exists to keep
out. To block another name:

```bash
python -c "import hashlib,sys;print(hashlib.sha256(sys.argv[1].lower().encode()).hexdigest())" Somename
```

## History

`git log` still holds the values this cleanup replaced: rewriting history was
out of scope, and a rewrite breaks every release anchor pinned in the Marketing
repository. The current tree is clean; the history is not. That trade-off is
recorded in the security audit, where the decision about repository visibility
is made.
