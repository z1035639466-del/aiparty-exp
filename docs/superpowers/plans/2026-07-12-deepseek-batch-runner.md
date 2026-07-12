# DeepSeek Batch Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and verify a safe DeepSeek batch generator that estimates cost before execution, records every attempt, retries invalid output, and reports validation by artifact generation.

**Architecture:** `run_ds.py` owns pure planning, naming, pricing, validation, and reporting helpers plus a thin HTTP client. `tests/test_run_ds.py` exercises all pure behavior and injects a fake transport for retry/logging tests, so no test sends API traffic.

**Tech Stack:** Python 3 standard library, `unittest`, existing `check.py`.

---

### Task 1: Batch planning, naming, and budget gate

**Files:**
- Create: `tests/test_run_ds.py`
- Create: `run_ds.py`

- [ ] Write failing tests for 36 planned calls, exact three-batch filenames, thinking fields, conservative cost estimates, and the preflight budget rejection.
- [ ] Run `python -m unittest tests.test_run_ds -v` and confirm imports or assertions fail because the implementation is absent.
- [ ] Implement immutable job records, batch construction, token/cost estimation, and the full-call budget gate.
- [ ] Re-run the focused tests and confirm they pass.

### Task 2: API attempts, retry preservation, and usage CSV

**Files:**
- Modify: `tests/test_run_ds.py`
- Modify: `run_ds.py`

- [ ] Add failing fake-transport tests proving request parameters, no `response_format`, independent messages, failure preservation, `_rN` naming, three-retry ceiling, and complete CSV columns.
- [ ] Run the focused tests and confirm expected failures.
- [ ] Implement HTTP transport, key rotation, response parsing, attempt persistence, JSON validation, retry loop, and append-only usage logging.
- [ ] Re-run the focused tests and confirm they pass.

### Task 3: CLI estimate/run modes and validation report

**Files:**
- Modify: `tests/test_run_ds.py`
- Modify: `run_ds.py`

- [ ] Add failing tests for estimate-only behavior, explicit run confirmation, DS-only validation, v11 historical labeling, v18 reporting, and usage summary totals.
- [ ] Run the focused tests and confirm expected failures.
- [ ] Implement CLI parsing, fixed `max_tokens`, estimate output, explicit `--run`, full `check.py` subprocess, grouped report, and final usage summary.
- [ ] Run `python -m unittest discover -s tests -v`, `python -m py_compile run_ds.py`, and `python run_ds.py --estimate`; confirm all succeed without API calls.

### Task 4: Repository handoff

**Files:**
- Verify only: repository status and diff

- [ ] Confirm no key material appears with `git diff --check` and a targeted `rg` scan.
- [ ] Commit the implementation and tests.
- [ ] Push `codex/ds-batch-runner` to `origin` and report synchronization.
- [ ] Stop after printing the cost estimate; do not run the paid batches until the user confirms that estimate.

