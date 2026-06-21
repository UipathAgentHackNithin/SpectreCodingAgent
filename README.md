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
7. **LLM Call 2 — Patch Generation** — LLM analyses candidate files (shown with line numbers) and returns a line-range patch, multi-range patch, or full rewrite depending on fix complexity.
8. **Validation & Commit** — Validates each replacement block as XML, applies hunks bottom-to-top to preserve line numbers, then commits via GitHub API.
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
| `process_name` | str | Process name including a numeric ID of 3+ digits (e.g. `3201 Invoice Processing`) |
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
cp .env.example .env  # set GITHUB_TOKEN, GITHUB_ORG, UIPATH_URL, UIPATH_PAT, UIPATH_REFRESH_TOKEN

# Run locally
uv run uipath run agent.py '{"transaction_id":"INV-001","process_name":"3201 Invoice Processing","diagnosis":"...","recommended_action":"..."}'

# Run tests
uv run pytest
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `GITHUB_TOKEN` | Yes (local) | PAT with `repo` scope for the target org — loaded from `GITHUB_TOKEN` credential asset on robot |
| `GITHUB_ORG` | No | GitHub organisation containing bot repos (default: `UipathAgentHackNithin`) |
| `UIPATH_URL` | Yes | Orchestrator base URL |
| `UIPATH_PAT` | Yes (local) | Personal Access Token for Orchestrator API — loaded from `SPECTRE_PAT` credential asset on robot |
| `UIPATH_REFRESH_TOKEN` | Yes (local) | Refresh token for LLM Gateway — loaded from `SPECTRE_REFRESH_TOKEN` credential asset on robot |

> On robot runtime, `GITHUB_TOKEN`, `UIPATH_PAT`, and `UIPATH_REFRESH_TOKEN` are **not** set as env vars — they are read at startup from Orchestrator credential assets via `sdk.assets.retrieve_credential()`. `UIPATH_ACCESS_TOKEN` is no longer used.

---

## Orchestrator Assets

| Asset | Type | Folder | Description |
|---|---|---|---|
| `GITHUB_TOKEN` | Credential | `Shared/Specter` | GitHub PAT with `repo` scope — read at agent startup |
| `SPECTRE_PAT` | Credential | `Shared/Specter` | Orchestrator Personal Access Token for API calls — read at agent startup |
| `SPECTRE_REFRESH_TOKEN` | Credential | `Shared/Specter` | Refresh token exchanged for an LLM-scoped JWT — read and written back at runtime |
| `SPECTRE_SUPPORT_HANDLE` | Text | `Shared/Specter` | Slack user group tag shown in failure messages (e.g. `<!subteam^S0BBTE9DA0N>`) |

> All credential assets must have **AllowDirectApiAccess** enabled in Orchestrator UI.

### Refreshing the LLM token

The agent **self-rotates** the refresh token on every run — the rotated token is automatically written back to the `SPECTRE_REFRESH_TOKEN` Orchestrator asset via PAT, so no manual refresh is needed for normal operation.

Run `refresh_token.ps1` from the project root only after publishing a new version to Orchestrator (publishing invalidates the current token):
1. Forces a fresh `uipath auth` login to obtain a new refresh token
2. Updates `UIPATH_REFRESH_TOKEN` and `UIPATH_ACCESS_TOKEN` in `.env` locally (no BOM)
3. Looks up the `SPECTRE_REFRESH_TOKEN` asset ID dynamically and writes the new token back as a Credential asset

---

## Non-Happy Path Handling

| Scenario | Behaviour |
|---|---|
| `GITHUB_TOKEN` not set | `find_repo_by_process` returns `None` → clean `FixOut` with support handle message |
| No repo found for process name | Returns `FixOut(fixed=False)` with user-facing message including support handle |
| Duplicate PR/issue exists | Returns existing URL, `is_duplicate=True`, no new PR created |
| LLM token unavailable | Returns `FixOut(fixed=False)` with support handle message |
| Branch creation fails | Returns `FixOut(fixed=False)` with support handle message |
| LLM fix analysis fails | Falls through to report-only PR — job does not crash |
| `replacement_lines` is invalid XML | Patch skipped, PR opened as report-only with skip reason |
| Line range out of bounds | Patch skipped, PR opened as report-only with skip reason |
| `rewritten_xaml` is malformed XML | Full rewrite skipped, PR opened as report-only with skip reason |
| `_commit_report` fails | Warning logged, PR creation continues — job does not crash |
| PR creation fails | Returns `FixOut(fixed=False)` with support handle message |
| LLM returns lowercase confidence | Normalised via `.capitalize()` before label assignment |
| Duplicate check on busy repo | Capped at 50 open PRs and 50 open issues to avoid rate limits |

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

The agent uses an **adaptive patch** approach: the LLM chooses the simplest mode that fixes the issue.

| `patch_mode` | When used | What the LLM returns |
|---|---|---|
| `line_range` | Single contiguous block (one activity, one expression, one attribute) | `start_line`, `end_line`, `replacement_lines` |
| `multi_range` | Two or more separate locations in the same file | `hunks[]` — each with `start_line`, `end_line`, `replacement_lines` |
| `full_rewrite` | Structural change (add/remove activity, restructure control flow) | `rewritten_xaml` (complete file) |

The LLM receives file content with **1-based line numbers** so it can reference exact positions without copying text.

**line_range / multi_range** are committed only when:
1. Every `replacement_lines` block is valid XML (validated with source file's `xmlns:` declarations)
2. All line ranges are within bounds and non-overlapping
3. Hunks are applied **bottom-to-top** so earlier line numbers remain valid after each splice

**full_rewrite** is committed only when:
1. `rewritten_xaml` is well-formed XML

If any validation fails, the PR is opened as **report-only** with the skip reason visible in the PR body.

### XAML Validation Rules Enforced

The LLM prompt enforces the following UiPath XAML correctness rules (sourced from `uipath-workflow-validation.mdc`):

1. **Well-formed XML** — no orphaned closing tags (e.g. stray `</Sequence>` after replacing a block)
2. **Preserve root metadata** — `sap:VirtualizedContainerService.HintSize` and all `xmlns:*` namespace declarations must be kept
3. **Preserve `TextExpression.ReferencesForImplementation`** exactly (e.g. `System.Data.DataSetExtensions` if DataTable/LINQ is used)
4. **CLR generics use `s:` not `x:`** — e.g. `scg:List(s:DateTime)` not `x:DateTime` inside generics
5. **No `Invoke Method`** — prefer `Assign` / `MultipleAssign` / `For Each Row` / `Invoke Workflow File`
6. **Config lookups** — resolve with one `Assign` + LINQ (`FirstOrDefault`/`LastOrDefault`) instead of `For Each` + nested `If`; never delete or invent `in_Config` keys
7. **Activity naming** — short action-oriented `DisplayName`; `sap2010:Annotation` on complex expressions
8. **Observability** — at least one `LogMessage` on entry for new workflows; `Warn`-level on handled failure paths
9. **Readability and maintainability** — shallow branching, prefer `MultipleAssign`, avoid redundant null-guard scaffolding for values known to always be present
10. **Unique `sap2010:WorkflowViewState.IdRef`** — every activity must have a unique IdRef; never copy-paste the same value; new activities get a new incremented IdRef
11. **VB expression syntax only** — `Nothing` not `null`, `String.IsNullOrEmpty` not `string.IsNullOrEmpty`, `AndAlso`/`OrElse` not `&&`/`||`, `Integer`/`String`/`Boolean` not `int`/`string`/`bool`
12. **Preserve argument direction** — `In`/`Out`/`InOut` on `x:Property` declarations must be kept exactly as in the original
13. **No truncation** — the complete file must be output; never abbreviate with `<!-- rest unchanged -->` or stop before the closing root tag

---

## Project Structure

```
src/spectre_coding/
    agent.py          # Main orchestration flow (FixIn → FixOut)
    llm.py            # LLM calls: file selection + patch generation
    github_client.py  # GitHub API: repo discovery, XAML fetch, PR creation
    xaml_scanner.py   # XAML metadata extraction for repo summary
    auth.py           # Token helpers (reads UIPATH_PAT and UIPATH_REFRESH_TOKEN from env; self-rotates refresh token)
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
