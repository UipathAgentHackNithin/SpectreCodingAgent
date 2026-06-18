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

1. **Input** - Receives the diagnosis payload produced by `SpectreInvestigationAgent` (exception type, activity name, suspected root cause, recommended fix action).
2. **Repo Discovery** - Uses the GitHub API to locate the target UiPath project repository within the organisation.
3. **XAML Retrieval** - Fetches the relevant workflow XAML file(s) via the GitHub Contents API.
4. **LLM Patch Generation** - Sends the broken XAML snippet plus the diagnosis to an LLM to generate a corrected version.
5. **PR Creation** - Commits the patched file to a new branch and opens a **draft Pull Request** so a developer can review before merging.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Runtime | Python 3.x |
| Agent Framework | UiPath Coded Workflows / Agent SDK |
| Source Control | GitHub REST API (PyGithub) |
| LLM Integration | UiPath AI / LLM connector |
| Orchestration | UiPath Orchestrator |

---

## Getting Started

### Prerequisites

- Python 3.9+
- UiPath Studio / Robot with Coded Workflows support
- GitHub Personal Access Token with `repo` scope
- UiPath Orchestrator connection configured

### Configuration

Set the following in UiPath Orchestrator Assets or environment variables:

| Asset | Description |
|---|---|
| `GITHUB_TOKEN` | PAT for reading/writing target bot repositories |
| `LLM_ENDPOINT` | UiPath AI Gateway or Azure OpenAI endpoint |
| `TARGET_ORG` | GitHub organisation containing the bot repos |

---

## Related Repositories

- [SpectreInvestigationAgent](../SpectreInvestigationAgent) - upstream diagnosis agent
