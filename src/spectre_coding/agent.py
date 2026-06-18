"""
SpectreCodingAgent — main orchestrator.

Flow:
  1. Extract process number from process_name, search GitHub org topics to find the repo.
  2. Clone the repo into a temp directory.
  3. Call LLM with the XAML + diagnosis → get a surgical XML patch.
  4. If patch found: apply it, push a branch, open a PR with full context.
  5. If no patch: open a "report-only" PR so a human developer has everything they need.
"""
import asyncio
import tempfile
import os
from pydantic import BaseModel

try:
    from .logger import get_logger
    from .auth import get_llm_token
    from .github_client import find_repo_by_process, clone_repo, push_branch, create_pull_request
    from .xaml_fixer import apply_llm_fix
except ImportError:
    from logger import get_logger
    from auth import get_llm_token
    from github_client import find_repo_by_process, clone_repo, push_branch, create_pull_request
    from xaml_fixer import apply_llm_fix

log = get_logger("spectre.coding_agent")


class FixIn(BaseModel):
    transaction_id: str
    process_name: str
    diagnosis: str
    recommended_action: str
    confidence: str = "Medium"


class FixOut(BaseModel):
    fixed: bool           # True = XAML was patched and PR contains a code change
    pr_url: str           # URL of the opened PR (always set when a repo was found)
    branch_name: str
    file_changed: str     # Repo-relative path of the XAML file
    fix_description: str  # Human-readable summary of what was done
    repo_full_name: str
    llm_confidence: str   # High / Medium / Low from the LLM
    message: str          # One-line status for the BPMN caller


async def fix(input: FixIn) -> FixOut:
    return await _run(input)


async def _run(input: FixIn) -> FixOut:
    log.info(
        f"SpectreCodingAgent — transaction={input.transaction_id} "
        f"process={input.process_name} confidence={input.confidence}"
    )

    # ── Step 1: Resolve GitHub repo ──────────────────────────────────────────
    repo_full_name = find_repo_by_process(input.process_name)
    if not repo_full_name:
        msg = f"No GitHub repo found for process: {input.process_name}"
        log.warning(msg)
        return FixOut(
            fixed=False, pr_url="", branch_name="", file_changed="",
            fix_description=msg, repo_full_name="", llm_confidence="Low", message=msg,
        )

    log.info(f"Repo resolved: {repo_full_name}")

    # ── Step 2 & 3: Clone, analyse, patch ────────────────────────────────────
    llm_token, base_url = get_llm_token()

    with tempfile.TemporaryDirectory() as tmp_dir:
        clone_repo(repo_full_name, tmp_dir)

        fix_result = await apply_llm_fix(
            repo_path=tmp_dir,
            access_token=llm_token,
            base_url=base_url,
            diagnosis=input.diagnosis,
            recommended_action=input.recommended_action,
            process_name=input.process_name,
            transaction_id=input.transaction_id,
        )

        # ── Step 4 / 5: Push branch and open PR ──────────────────────────────
        branch_name = f"spectre-fix/{input.transaction_id.lower()}"
        commit_msg = _build_commit_message(input, fix_result)
        push_branch(tmp_dir, branch_name, commit_msg)

    pr_title = _build_pr_title(input, fix_result)
    pr_body = _build_pr_body(input, fix_result)
    pr_url = create_pull_request(repo_full_name, branch_name, pr_title, pr_body)

    fix_description = fix_result["explanation"]
    log.info(f"PR opened: {pr_url} | fixed={fix_result['fixed']} llm_confidence={fix_result['llm_confidence']}")

    return FixOut(
        fixed=fix_result["fixed"],
        pr_url=pr_url,
        branch_name=branch_name,
        file_changed=fix_result["file_changed"],
        fix_description=fix_description,
        repo_full_name=repo_full_name,
        llm_confidence=fix_result["llm_confidence"],
        message=f"PR opened: {pr_url}" + (" (code changed)" if fix_result["fixed"] else " (report only — manual fix needed)"),
    )


# ── PR / commit message builders ─────────────────────────────────────────────

def _build_commit_message(input: FixIn, fix_result: dict) -> str:
    if fix_result["fixed"]:
        return f"[SpectreAI] Fix {input.transaction_id}: {fix_result['explanation'][:72]}"
    return f"[SpectreAI] Diagnosis report for {input.transaction_id} (no code change)"


def _build_pr_title(input: FixIn, fix_result: dict) -> str:
    tag = "Fix" if fix_result["fixed"] else "Report"
    return f"[SpectreAI {tag}] {input.process_name} — {input.transaction_id}"


def _build_pr_body(input: FixIn, fix_result: dict) -> str:
    status = "Code change applied" if fix_result["fixed"] else "Report only — manual fix required"
    diff_section = ""
    if fix_result["fixed"] and fix_result.get("original_snippet"):
        diff_section = (
            "\n### XAML Change\n"
            f"**File:** `{fix_result['file_changed']}`\n\n"
            "**Before:**\n```xml\n"
            f"{fix_result['original_snippet']}\n"
            "```\n\n"
            "**After:**\n```xml\n"
            f"{fix_result['replacement_snippet']}\n"
            "```\n"
        )
    elif not fix_result["fixed"] and fix_result.get("original_snippet"):
        # LLM found a fix but snippet wasn't located verbatim — show it anyway for the developer
        diff_section = (
            "\n### Proposed Change (could not be applied automatically)\n"
            f"**File:** `{fix_result['file_changed']}`\n\n"
            "**Replace this:**\n```xml\n"
            f"{fix_result['original_snippet']}\n"
            "```\n\n"
            "**With this:**\n```xml\n"
            f"{fix_result['replacement_snippet']}\n"
            "```\n"
        )

    return (
        f"## SpectreAI Coding Agent — {status}\n\n"
        f"| Field | Value |\n"
        f"|---|---|\n"
        f"| Transaction ID | `{input.transaction_id}` |\n"
        f"| Process | {input.process_name} |\n"
        f"| Investigation confidence | {input.confidence} |\n"
        f"| LLM fix confidence | {fix_result['llm_confidence']} |\n\n"
        f"### Diagnosis\n{input.diagnosis}\n\n"
        f"### Recommended Action\n{input.recommended_action}\n\n"
        f"### Fix Analysis\n{fix_result['explanation']}"
        f"{diff_section}\n"
        f"---\n*Opened automatically by SpectreCodingAgent — review before merging*"
    )


if __name__ == "__main__":
    result = asyncio.run(fix(FixIn(
        transaction_id="INV-98766",
        process_name="3201 Invoice Processing",
        diagnosis="SAP login failed due to credential timeout on the authentication step",
        recommended_action="Replace hardcoded SAP password with Get Credential activity reading from 'SAPCredentials' asset",
        confidence="High",
    )))
    print(result)
