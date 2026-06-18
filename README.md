# SpectreCodingAgent

## Overview

**SpectreCodingAgent** is a Python-based UiPath coded agent and a core component of the **SpectreAI** autonomous RPA bot self-healing system. It acts as the *fix engine*: given a structured diagnosis of a bot failure, it locates the broken code, engineers a patch using an LLM, and opens a draft Pull Request for human review.

---

## Role in the SpectreAI System

SpectreAI is a two-agent pipeline for autonomous RPA bot repair:

| Agent | Responsibility |
|---|---|
| **SpectreInvestigationAgent** | Pulls Orchestrator logs, analyses exceptions, diagnoses root cause, produces a structured fix recommendation |
| **SpectreCodingAgent** *(this repo)* | Consumes the diagnosis, fetches XAML source from GitHub, applies the LLM-generated patch, opens a draft PR |

```
Bot Failure
    |
    v
SpectreInvestigationAgent --> Structured Diagnosis (JSON)
                                        |
                                        v
                            SpectreCodingAgent
                                        |
                              +---------+---------+
                              |                   |
                         GitHub API           LLM (patch)
                         (fetch XAML)              |
                              +---------+----------+
                                        |
                                        v
                                  Draft Pull Request
```

---

## Architecture

1. **Input** — Receives the diagnosis payload produced by `SpectreInvestigationAgent` (exception type, activity name, suspected root cause, recommended fix action).
2. **Repo Discovery** — Uses the GitHub search API to locate the target UiPath project repository within the organisation by process number topic.
3. **Duplicate Check** — Skips if an open PR or issue already exists for the same transaction ID.
4. **XAML Listing** — Fetches all `.xaml` file paths (no content yet) via the GitHub Contents API.
5. **LLM Call 1 — File Selection** — LLM picks the 1–3 candidate files most likely to contain the root cause, based on file paths and repo structure.
6. **XAML Content Fetch** — Fetches full content for candidate files only (not the whole repo).
7. **LLM Call 2 — Patch Generation** — LLM analyses the candidate files and produces a surgical XML fix: `original_snippet` → `replacement_snippet`.
8. **Patch Validation & Apply** — Validates replacement XML, verifies the original snippet exists verbatim in the file, applies the patch in-memory and commits via API.
9. **Draft PR** — Opens a draft Pull Request with labels, assignee from CODEOWNERS, and a detailed body including the XML diff or patch-skip reason.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Runtime | Python 3.12 |
| Agent Framework | UiPath Coded Workflows / Agent SDK |
| Source Control | GitHub REST API (PyGithub) |
| LLM Integration | UiPath LLM Gateway (GPT-4.1 mini) |
| Orchestration | UiPath Orchestrator |

---

## Input / Output

### Input (`FixIn`)

| Field | Type | Description |
|---|---|---|
| `transaction_id` | str | Orchestrator transaction ID (e.g. `INV-98766`) |
| `process_name` | str | Process name including 4-digit number (e.g. `3201 Invoice Processing`) |
| `diagnosis` | str | Structured diagnosis from SpectreInvestigationAgent |
| `recommended_action` | str | Suggested fix from SpectreInvestigationAgent |
| `confidence` | str | Investigation confidence: `High / Medium / Low` |

### Output (`FixOut`)

| Field | Type | Description |
|---|---|---|
| `fixed` | bool | `True` if a code patch was committed |
| `pr_url` | str | URL of the draft PR |
| `branch_name` | str | Branch created (e.g. `spectre-fix/inv-98766-20250618120000`) |
| `file_changed` | str | Relative path of the patched file |
| `target_activity` | str | DisplayName of the fixed activity |
| `fix_description` | str | LLM explanation of the fix |
| `llm_confidence` | str | LLM fix confidence: `High / Medium / Low` |
| `is_duplicate` | bool | `True` if a PR/issue already existed for this transaction |
| `message` | str | Human-readable summary |

---

## Local Setup

```bash
# Install dependencies
uv sync

# Copy and configure environment
cp .env.example .env  # set GITHUB_TOKEN, GITHUB_ORG, UIPATH_URL, UIPATH_ACCESS_TOKEN

# Run locally
uv run uipath run agent.py '{"transaction_id":"INV-001","process_name":"3201 Invoice Processing","diagnosis":"...","recommended_action":"..."}'

# Run tests
uv run pytest
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `GITHUB_TOKEN` | Yes | PAT with `repo` scope for the target org |
| `GITHUB_ORG` | Yes | GitHub organisation containing bot repos |
| `UIPATH_URL` | Yes | Orchestrator base URL |
| `UIPATH_ACCESS_TOKEN` | Yes (robot) | Robot access token for LLM Gateway |
| `SPECTRE_DEFAULT_ASSIGNEE` | No | Fallback PR assignee if no CODEOWNERS (default: `brnithin33-AI`) |

> On robot runtime, set these as Orchestrator robot environment variables — `.env` is not available.

---

## PR Labels

The agent auto-creates and applies these labels:

| Label | When |
|---|---|
| `bug` | Always |
| `spectre-ai` | Always |
| `sap / api / ui-automation / data / unknown` | Based on LLM issue classification |
| `needs-human-review` | When confidence is Low or no automated fix was applied |

---

## Patch Apply Logic

The agent applies patches only when:
1. `replacement_snippet` is valid XML (`ET.fromstring` check)
2. `original_snippet` is found verbatim in the target file

If either check fails, the PR is opened as **report-only** with the skip reason visible in the PR body (e.g. `Patch not applied: original_snippet not found verbatim — possible whitespace drift or truncation`).

---

## Project Structure

```
src/spectre_coding/
    agent.py          # Main orchestration flow (FixIn → FixOut)
    llm.py            # LLM calls: file selection + patch generation
    github_client.py  # GitHub API: repo discovery, XAML fetch, PR creation
    xaml_scanner.py   # XAML metadata extraction for repo summary
    auth.py           # Token helpers
    logger.py         # Shared logger

tests/
    test_agent.py         # Flow + patch-apply + PR body tests
    test_github_client.py # GitHub client unit tests
    test_llm.py           # LLM parse + validation tests
    test_xaml_scanner.py  # XAML scanner tests
    test_xaml_fixer.py    # Patch logic tests
```

---

## Related Repositories

- [SpectreInvestigationAgent](../SpectreInvestigationAgent) — upstream diagnosis agent
