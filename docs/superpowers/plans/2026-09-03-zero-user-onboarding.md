# Zero-User Onboarding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn README into a zero-technical-knowledge onboarding surface and minimally align setup policy with persistent/multi-book workspace guidance.

**Architecture:** README remains non-normative and user-first. `docs/AGENT_SETUP.md` receives only workspace-selection invariants required to make README claims true. Contract tests enforce the onboarding information architecture without moving execution rules back into README.

**Tech Stack:** Markdown documentation, Python `unittest` contract tests, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-03-zero-user-onboarding-design.md`

## Global Constraints

- README is not part of the agent execution contract.
- Do not use chapter count as the criterion for choosing persistent versus transient work.
- A workspace may contain multiple books under `books/<book-slug>/`.
- Permanent per-book Git branches are not the default storage model.
- Do not promise cloud persistence, repository creation, shell, Git, or file-writing capabilities unless available.
- Do not change literary quality, chapter-state semantics, CLI behavior, or runtime dependencies.

---

### Task 1: Define onboarding contract tests

**Files:**
- Modify: `tests/test_agent_contract.py`

**Interfaces:**
- Consumes: current README/setup contract.
- Produces: regression tests for user-facing onboarding and workspace policy.

- [ ] Add a test requiring README sections/phrases for `Start in 30 seconds`, three usage modes, private GitHub recommendation, local use, multi-book storage, resume, FAQ, privacy, and internal architecture.
- [ ] Add assertions that README does not use a fixed chapter-count threshold for choosing a mode.
- [ ] Add a setup-contract test requiring persistent workspace guidance, multi-book support, optional branch isolation, and capability honesty.
- [ ] Run `python -m unittest discover -s tests -v` and confirm the new tests fail against the old README/setup wording.
- [ ] Commit as `test: define zero-user onboarding contract`.

### Task 2: Rewrite README for a nontechnical first-time user

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: onboarding spec and existing technical reference content.
- Produces: user-first README that remains non-normative.

- [ ] Replace the opening with a plain-language product promise and explicit no-programming/no-API-key messaging.
- [ ] Add `Start in 30 seconds` with a copy/paste prompt.
- [ ] Add a three-mode chooser: Web AI, Private GitHub workspace (recommended for full/multi-session books), Local workspace.
- [ ] Explain each mode with agent-managed setup first and manual fallback second where applicable.
- [ ] Add multi-book storage and resume examples.
- [ ] Add user-facing workflow steps and FAQ.
- [ ] Move role routing, authority files, CLI, project structure, and tests below `How it works internally`.
- [ ] Preserve privacy/copyright warnings and make them visible before advanced technical material.
- [ ] Run the full test suite.
- [ ] Commit as `docs: make README beginner-first`.

### Task 3: Align setup policy with persistent workspace onboarding

**Files:**
- Modify: `docs/AGENT_SETUP.md`

**Interfaces:**
- Consumes: README modes.
- Produces: authoritative setup rules supporting transient and persistent installation choices.

- [ ] Add a `Choose workspace persistence` section after capability detection.
- [ ] State that transient web work is acceptable only when persistence is not required and the environment can safely perform the requested scope.
- [ ] Prefer a persistent writable workspace for full-book/multi-session work when available.
- [ ] State that one installation may host many `books/<book-slug>/` workspaces.
- [ ] State that permanent per-book branches are not required; optional branch/worktree isolation may be used for concurrent advanced workflows.
- [ ] Reassert that repository/cloud persistence creation must not be claimed without the necessary capability.
- [ ] Run the full test suite.
- [ ] Commit as `docs: define persistent workspace setup policy`.

### Task 4: Final verification

**Files:**
- Review: `README.md`, `docs/AGENT_SETUP.md`, `tests/test_agent_contract.py`

**Interfaces:**
- Produces: merge-ready onboarding update on the existing PR.

- [ ] Run `python -m unittest discover -s tests -v` on the final branch.
- [ ] Verify GitHub Actions on Python 3.10 and 3.12.
- [ ] Review PR diff to confirm no runtime/CLI/literary-contract changes were introduced by this onboarding pass.
- [ ] Update PR #4 description with the beginner-onboarding additions and verification evidence.