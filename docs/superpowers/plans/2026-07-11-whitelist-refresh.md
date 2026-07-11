# Whitelist Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the v1.6 skill source, refresh the JSON whitelist exactly, add the supplied D input, validate existing outputs, and commit only these changes.

**Architecture:** `whitelist.json` remains the single source of allowed props, mechanics, and visibility values used by `check.py`. The supplied Markdown and input JSON are independent artifacts; `check.py` is executed read-only against existing output JSON files.

**Tech Stack:** Markdown, JSON, Python standard library, Git.

---

### Task 1: Refresh supplied artifacts

**Files:**
- Create: `DM-skill-开局生成-v1.6.md`
- Modify: `whitelist.json`
- Create: `inputs/input_D.json`

- [ ] **Step 1: Copy the supplied v1.6 Markdown into the repository root**

Run: `Copy-Item -LiteralPath 'C:\Users\10356\Downloads\DM-skill-开局生成-v1.6.md' -Destination '.\DM-skill-开局生成-v1.6.md'`

- [ ] **Step 2: Replace whitelist values with the source’s 14 props, three visibility levels, and exact 15 mechanics**

Write a UTF-8 JSON document with `props`, `mechanics`, and `visibility` arrays. The three penalty mechanics must be the distinct values `惩罚(轻)`, `惩罚(中)`, and `惩罚(重)`.

- [ ] **Step 3: Create the supplied input JSON**

Write `inputs/input_D.json` as UTF-8 JSON containing the requested venue, four players, taste sliders, and four materials.

### Task 2: Verify and commit

**Files:**
- Verify: `whitelist.json`
- Verify: `inputs/input_D.json`
- Verify: `outputs/*.json`

- [ ] **Step 1: Parse and print the whitelist exactly**

Run: `python -c "import json; print(json.dumps(json.load(open('whitelist.json', encoding='utf-8')), ensure_ascii=False, indent=2))"`

- [ ] **Step 2: Check the existing outputs without editing them**

Run: `python check.py`

Expected: nonzero status is acceptable only for the known compound-visibility specification disagreement; no output files are changed.

- [ ] **Step 3: Verify scope and commit**

Run: `git diff --check; git status --short; git add DM-skill-开局生成-v1.6.md whitelist.json inputs/input_D.json docs/superpowers/plans/2026-07-11-whitelist-refresh.md; git commit -m "chore: refresh opening generation whitelist"`
