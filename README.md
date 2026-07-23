# SpectreCodingAgent

Python Coded Agent for SpectreAI — the AI-powered RPA support automation system built for UiPath AgentHack 2026.

SpectreCodingAgent patches broken XAML files via the GitHub Contents API (no repo cloning), validates the XML, commits, and raises a labelled Draft PR — all in under 2 minutes from a Slack message.

---

## Project Description

SpectreCodingAgent is triggered by the Maestro orchestration layer when SpectreInvestigationAgent returns a high-confidence code bug diagnosis.

The agent:
1. Finds the GitHub repo by process ID (searches org repos by topic)
2. Checks for duplicate PRs to avoid double-patching
3. Fetches all XAML files via **GitHub Contents API — no cloning**
4. Sends candidate files to the reasoning engine to select the most relevant
5. Reasoning engine produces a **surgical patch** — not a rewrite, a targeted fix
6. **Validates the XML** before any commit — broken XAML never touches the repo
7. Commits the patched file via SHA-based GitHub API call
8. Raises a **labelled Draft PR** with root cause, confidence score, and diff in the description
9. Notifies both the support channel and dev channel in Slack

If the patch cannot be safely produced (multi-file fix, low patch confidence), a report-only PR is raised with just the diagnosis — no code changes.

---

## UiPath Components Used

| Component | Usage |
|---|---|
| **UiPath Coded Agents (Python)** | This agent is implemented as a Python Coded Agent |
| **UiPath Maestro** | Triggers this agent with diagnosis context from InvestigationAgent |
| **UiPath Integration Service — Slack** | Posts PR notification to support and dev channels |
| **UiPath Orchestrator** | Asset management (GitHub PAT, Slack tokens) |

---

## Agent Type

**Coded Agent (Python)**

This is a fully coded agent written in Python. It uses:
- `uipath` Python SDK for Orchestrator asset access
- `requests` / GitHub REST API for all repo operations (no `git` CLI, no cloning)
- `xml.etree.ElementTree` for XML validation before commit
- `openai` client for GPT-4.1 mini reasoning engine calls
- `slack_sdk` for Slack notifications

No low-code components — entirely Python.

---

## Repository Structure

```
SpectreCodingAgent/
├── src/
│   └── spectre_coding/
│       ├── agent.py          # Main agent orchestration logic
│       ├── github_client.py  # GitHub Contents API — fetch, commit, PR creation
│       ├── llm.py            # Reasoning engine (GPT-4.1 mini) calls
│       ├── xaml_scanner.py   # XAML file selection and XML validation
│       ├── slack_client.py   # Slack notification handler
│       └── auth.py           # Token management
├── tests/                    # Unit tests
├── uipath.json               # UiPath Coded Agent configuration
├── requirements.txt          # Python dependencies
└── README.md
```

---

## Setup Instructions

### Prerequisites

- Python 3.11+
- UiPath Orchestrator (cloud) account
- GitHub PAT with `repo` scope (read + write)
- Slack Bot Token with `chat:write`, `channels:read` permissions
- OpenAI API key (GPT-4.1 mini)

### Step 1 — Clone the repository

```bash
git clone https://github.com/UipathAgentHackNithin/SpectreCodingAgent.git
cd SpectreCodingAgent
```

### Step 2 — Install dependencies

```bash
pip install -r requirements.txt
```

### Step 3 — Configure Orchestrator assets

Create the following assets in UiPath Orchestrator:

| Asset Name | Purpose |
|---|---|
| `GITHUB_PAT` | GitHub Personal Access Token with repo scope |
| `SLACK_BOT_TOKEN` | Slack Bot OAuth token |
| `SLACK_SUPPORT_CHANNEL_ID` | Slack support channel ID |
| `SLACK_DEV_CHANNEL_ID` | Slack dev channel ID for PR notifications |

### Step 4 — Tag your target repos in GitHub

The agent discovers repos by process ID using GitHub topic tags. For each target bot repo, add a topic tag in the format `process-XXXX` (e.g. `process-3201`) in the repo settings on GitHub.

### Step 5 — Deploy as a Function process in Orchestrator

1. Open UiPath Studio
2. Open this project
3. Publish to Orchestrator as a **Function** process type
4. The process will be triggered by the Maestro orchestration layer with diagnosis context

### Step 6 — Test

Trigger the Maestro process via the Slack shortcut with a Bug submission for a process that has a tagged repo. Verify:
- CodingAgent job starts in Orchestrator after InvestigationAgent completes
- Draft PR appears on GitHub with correct labels and description
- Support and dev channels receive Slack notifications

---

## Key Design Decisions

**No repo cloning** — every file operation goes through the GitHub Contents API. This means no local disk usage, no stale code, and the agent can run in any environment without git installed.

**XML validation before commit** — the patched XAML is validated with `xml.etree.ElementTree` before any write to GitHub. If validation fails, the agent falls back to a report-only PR.

**Confidence-gated patching** — the agent only commits when patch confidence is high. Ambiguous fixes produce a diagnosis-only PR, leaving the decision to the developer.

---

## Related Repositories

| Repository | Description |
|---|---|
| [SpectreAI-Maestro](https://github.com/UipathAgentHackNithin/SpectreAI-Maestro) | Maestro orchestration layer — triggers this agent |
| [SpectreInvestigationAgent](https://github.com/UipathAgentHackNithin/SpectreInvestigationAgent) | Python Coded Agent — log fetch and diagnosis |
| [InvoiceProcessing-Performer](https://github.com/UipathAgentHackNithin/InvoiceProcessing-Performer) | Sample target bot used in demo |

---

## Demo

- **Demo Video:** https://www.youtube.com/watch?v=d64LqEl6M5Y
- **Devpost:** https://devpost.com/software/zeroday
- **UiPath Forum:** https://forum.uipath.com/t/spectreai-from-bots-down-in-slack-to-a-draft-pr-in-under-2-minutes-agenthack-2026/5755787

---

## Author

Nithin BR — Agentic Architect @ Persistent Systems
UiPath AgentHack 2026
