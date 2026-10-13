# RCV-13 GUI recovery mismatch correction — verification

Checkpoint: `c5cf4282a769377eefd97a46e40c8c6a9f3e4140`  
Parent: `5bbd5ab4a1d10967194060a2941ec351ab7ce81e`

## Scope

An action ID that already has a committed GUI receipt must never be reported as
a confirmed, discardable client rejection merely because a retry changes the
explicit speech recipient or statement.  `PlayerDashboard.action()` now raises
`GuiRecoveryError` for that mismatch; the WSGI layer returns the existing
neutral recovery `409` payload without `confirmed_rejection`.  The browser's
existing 409 path therefore preserves its pending request and asks the player
to inspect saved state or explicitly end recovery.

The regression creates a campaign, commits an explicit statement, cold-reopens
it, and sends the changed statement through `/api/action`.  It asserts `409`,
the exact neutral message, and absence of `confirmed_rejection`.  No provider
or turn-generation path is invoked for that recovered mismatch.

## Validation

* `tests/app/test_explicit_speech.py tests/app/test_gui.py tests/test_action_integrity.py -rA`
  — `114 passed in 0.86s`.
* `/tmp/llm-rpg-engine-testenv/bin/python -m pytest -o "addopts=-m 'not slow'" -rA`
  — `1900 passed, 1 deselected in 27.27s`.
  The deselection is the pre-existing slow real-embedding test; no real model,
  embedding, network call, profiling, B1 retrieval, deployment, push, or PR
  was performed.
* `git bundle verify review_material/RCV-13-c5cf428-full.bundle` reports a
  complete history and succeeds.
* A fresh clone of that bundle was clean at `c5cf428` and ran
  `tests/app/test_explicit_speech.py -rA`: `2 passed in 0.28s`.

## Artifacts

| File | SHA-256 |
| --- | --- |
| `RCV-13-c5cf428-full.bundle` | `24443b2c5efcf34f89afcfb50f10bd29dd3d11da9ee227978e9578b105d109f0` |
| `RCV-13-c5cf428-source.tar.gz` | `8a9ea71e93ac651bbd94b287b75f9255e5c5c418cab996ea3b05d87549974b57` |
| `RCV-13-c5cf428-from-5bbd5ab.diff` | `fe341d05669b7afcb6dc55fcdba746ecf928d3a592feea40f8afe6091d6cc7f2` |

The source archive scan found only the tracked template `.env.local.example`;
no database, SQLite, JSONL, or active credential file was archived. `git diff
--check` is clean. The tracked checkout is clean after the commit; retained
delivery and historical evidence files remain intentionally untracked.

## Browser-evidence limit

The earlier Chromium/Playwright evidence for explicit speech only proved that
the recipient and statement controls and their prompt text rendered on the
loopback page. It did **not** exercise action publication or recovery.

After this checkpoint was created, a separate loopback-only Chromium check
completed the browser interaction boundary using a temporary campaign with one
public co-located innkeeper and a deterministic fake provider. It did not use a
real model or claim narrative quality:

1. The real form initially reported invalid while the required action field was
   empty. The page exposed only the public innkeeper as the extra recipient.
   Native input/change events plus a click on the submit button produced an
   observed `POST /api/action 200`; the browser displayed `行动已保存（版本 3）。`.
2. The server process was stopped, the same campaign reopened in a fresh
   dashboard/server, and the browser page refreshed. Restoring the original
   pending envelope and clicking its visible recovery control produced another
   observed `POST /api/action 200`, displayed `已恢复此前保存的行动。`, and kept
   revision 3.
3. Changing only the pending statement and clicking the same browser recovery
   control produced an observed `POST /api/action 409`. The page displayed
   `该行动已提交但无法安全恢复结果；请查看当前状态后明确结束恢复。`, kept the pending
   envelope, and left revision 3 unchanged.

The test data, browser profile, and loopback server were all temporary and
were removed after the check. No campaign database, JSONL, screenshot, or
browser profile was included in any recovery artifact. This is UI protocol and
persistence evidence, not real-model gameplay evidence.
