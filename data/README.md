# Seed data

Automations and button pages exported from the 2026-11-21 event database (taken from the event
Pi's dump), written with `tools/automation-sync/sync.py`. Import them into a fresh server with
`sync.py import --dry-run` first.

Deliberate differences from what ran at the event:

- Every automation that drives the ATEM or the WLED strip is **disabled** (those integrations were
  disabled/offline); enable them once the hardware is real.
- "Match start: start the recording" now triggers on the audience display going to `IN_MATCH`
  (starting from `field_id`, see tmtools #126) instead of `matchStarted`, and is enabled.
- "Match start: stage lights to the match look" still points at a Zeros preset (`Match`) that did not
  exist at the event; create it or disable the automation.
- Integrations (`obs.main`, `capture.main`, `vex_tm.*`) are not exported: their config holds secrets.
  Create `obs.main` and `capture.main` on a new server before importing automations that use them.
