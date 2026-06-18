"""
SpectreCodingAgent — orchestrates the full fix flow.

1. Find GitHub repo by process number topic
2. Check for duplicate PRs/issues
3. Clone repo, scan all XAML files
4. LLM call 1: select candidate files from repo summary
5. LLM call 2: analyse candidates and produce surgical fix
6. Apply patch if possible
7. Push branch, open DRAFT PR with labels + assignee
"""
import asyncio
import os
import tempfile
from pydantic import BaseModel

try:
    from .logger import get_logger
    from .auth import get_llm_token
    from .github_client import (
        find_repo_by_process, check_duplicate, clone_repo,
        push_branch, create_draft_pr, get_codeowner,
    )
    from .xaml_scanner import scan_repo_xamls, build_repo_summary
    from .llm import select_target_files, analyse_and_fix
except ImportError:
    from logger import get_logger
    from auth import get_llm_token
    from github_client import (
        find_repo_by_process, check_duplicate, clone_repo,
        push_branch, create_draft_pr, get_codeowner,
    )
    from xaml_scanner import scan_repo_xamls, build_repo_summary
    from llm import select_target_files, analyse_and_fix

log = get_logger("spectre.coding_agent")


class FixIn(BaseModel):
    transaction_id: str
    process_name: str
    diagnosis: str
    recommended_action: str
    confidence: str = "Medium"


class FixOut(BaseModel):
    fixed: bool
    pr_url: str
    repo_full_name: str
    branch_name: str
    file_changed: str
    target_activity: str
    fix_description: str
    llm_confidence: str
    is_duplicate: bool
    message: str


async def fix(input: FixIn) -> FixOut:
    log.info(f"SpectreCodingAgent — txn={input.transaction_id} process={input.process_name}")

    # ── 1. Find repo ──────────────────────────────────────────────────────────
    repo_full_name = find_repo_by_process(input.process_name)
    if not repo_full_name:
        msg = f"No GitHub repo found for process: {input.process_name}"
        log.warning(msg)
        return _empty_out(msg)

    # ── 2. Duplicate check ────────────────────────────────────────────────────
    existing_url = check_duplicate(repo_full_name, input.transaction_id)
    if existing_url:
        log.info(f"Duplicate found — skipping: {existing_url}")
        return FixOut(
            fixed=False, pr_url=existing_url, repo_full_name=repo_full_name,
            branch_name="", file_changed="", target_activity="",
            fix_description="Duplicate — existing PR/issue found",
            llm_confidence="", is_duplicate=True,
            message=f"Duplicate found: {existing_url}",
        )

    llm_token, base_url = get_llm_token()

    with tempfile.TemporaryDirectory() as tmp_dir:
        # ── 3. Clone + scan ───────────────────────────────────────────────────
        clone_repo(repo_full_name, tmp_dir)
        scan_results = scan_repo_xamls(tmp_dir)
        repo_summary = build_repo_summary(scan_results)
        log.info(f"Scanned {len(scan_results)} XAML files")

        # ── 4. LLM call 1: file selection ─────────────────────────────────────
        selection = await select_target_files(llm_token, base_url, input.diagnosis, repo_summary)
        candidates = selection.get("candidates", [])[:3]
        selection_confidence = selection.get("confidence", "Low")
        log.info(f"File selection: candidates={candidates} confidence={selection_confidence}")

        if not candidates:
            log.warning("LLM could not identify candidate files — opening report-only PR")
            fix_result = _no_fix_result("LLM could not identify the relevant XAML file from repo structure", "Low")
        else:
            # ── 5. Read candidate file contents ───────────────────────────────
            candidate_files = {}
            path_map = {r["path"]: r for r in scan_results}
            for c in candidates:
                # normalise path separators
                norm = c.replace("/", os.sep).replace("\\", os.sep)
                full = os.path.join(tmp_dir, norm)
                if os.path.exists(full):
                    with open(full, "r", encoding="utf-8") as fh:
                        candidate_files[c] = fh.read()
                else:
                    log.warning(f"Candidate file not found on disk: {c}")

            if not candidate_files:
                fix_result = _no_fix_result("Candidate files identified but could not be read", "Low")
            else:
                # ── 6. LLM call 2: analyse and fix ────────────────────────────
                fix_result = await analyse_and_fix(
                    llm_token, base_url,
                    input.diagnosis, input.recommended_action,
                    candidate_files,
                )
                log.info(f"Fix analysis: can_fix={fix_result.get('can_fix')} confidence={fix_result.get('confidence')}")

                # Apply patch if possible
                if fix_result.get("can_fix") and fix_result.get("original_snippet"):
                    target_file = fix_result.get("target_file", "")
                    norm = target_file.replace("/", os.sep).replace("\\", os.sep)
                    full_target = os.path.join(tmp_dir, norm)
                    if os.path.exists(full_target):
                        with open(full_target, "r", encoding="utf-8") as fh:
                            content = fh.read()
                        original = fix_result["original_snippet"]
                        if original in content:
                            patched = content.replace(original, fix_result["replacement_snippet"], 1)
                            with open(full_target, "w", encoding="utf-8") as fh:
                                fh.write(patched)
                            fix_result["_actually_patched"] = True
                            log.info(f"Patch applied to {target_file}")
                        else:
                            fix_result["_actually_patched"] = False
                            log.warning("original_snippet not found verbatim — patch skipped")
                    else:
                        fix_result["_actually_patched"] = False

        # ── 7. Push branch ────────────────────────────────────────────────────
        branch_name = f"spectre-fix/{input.transaction_id.lower()}"
        commit_msg = fix_result.get("commit_message") or f"[SpectreAI] Diagnosis report for {input.transaction_id}"
        push_branch(tmp_dir, branch_name, commit_msg)

    # ── Build labels ──────────────────────────────────────────────────────────
    llm_confidence = fix_result.get("confidence", "Low")
    issue_type_label = fix_result.get("issue_type_label", "unknown")
    labels = ["bug", "spectre-ai", issue_type_label]
    if llm_confidence == "Low" or not fix_result.get("can_fix"):
        labels.append("needs-human-review")

    # ── Assignee from CODEOWNERS ──────────────────────────────────────────────
    assignee = get_codeowner(repo_full_name)

    # ── Open draft PR ─────────────────────────────────────────────────────────
    actually_patched = fix_result.get("_actually_patched", False)
    pr_title = _build_pr_title(input, fix_result, actually_patched)
    pr_body = _build_pr_body(input, fix_result, actually_patched)
    pr_url = create_draft_pr(repo_full_name, branch_name, pr_title, pr_body, labels, assignee)

    log.info(f"Draft PR opened: {pr_url}")
    return FixOut(
        fixed=actually_patched,
        pr_url=pr_url,
        repo_full_name=repo_full_name,
        branch_name=branch_name,
        file_changed=fix_result.get("target_file", ""),
        target_activity=fix_result.get("target_activity", ""),
        fix_description=fix_result.get("explanation", ""),
        llm_confidence=llm_confidence,
        is_duplicate=False,
        message=f"Draft PR opened: {pr_url}" + (" (code patched)" if actually_patched else " (report only)"),
    )


def _no_fix_result(reason: str, confidence: str) -> dict:
    return {
        "can_fix": False,
        "target_file": "",
        "target_activity": "",
        "original_snippet": "",
        "replacement_snippet": "",
        "explanation": reason,
        "commit_message": "",
        "confidence": confidence,
        "issue_type_label": "unknown",
        "_actually_patched": False,
    }


def _empty_out(message: str) -> FixOut:
    return FixOut(
        fixed=False, pr_url="", repo_full_name="", branch_name="",
        file_changed="", target_activity="", fix_description=message,
        llm_confidence="", is_duplicate=False, message=message,
    )


def _build_pr_title(input: FixIn, fix_result: dict, patched: bool) -> str:
    tag = "Fix" if patched else "Report"
    return f"[SpectreAI {tag}] {input.process_name} — {input.transaction_id}"


def _build_pr_body(input: FixIn, fix_result: dict, patched: bool) -> str:
    status = "Code change applied" if patched else "Report only — manual fix required"
    diff_section = ""

    if patched and fix_result.get("original_snippet"):
        diff_section = (
            f"\n### XAML Change\n"
            f"**File:** `{fix_result['target_file']}`  \n"
            f"**Activity:** `{fix_result.get('target_activity', 'unknown')}`\n\n"
            f"**Before:**\n```xml\n{fix_result['original_snippet']}\n```\n\n"
            f"**After:**\n```xml\n{fix_result['replacement_snippet']}\n```\n"
        )
    elif not patched and fix_result.get("original_snippet"):
        diff_section = (
            f"\n### Proposed Change (apply manually)\n"
            f"**File:** `{fix_result['target_file']}`  \n"
            f"**Activity:** `{fix_result.get('target_activity', 'unknown')}`\n\n"
            f"**Replace:**\n```xml\n{fix_result['original_snippet']}\n```\n\n"
            f"**With:**\n```xml\n{fix_result['replacement_snippet']}\n```\n"
        )

    return (
        f"## SpectreAI Coding Agent — {status}\n\n"
        f"> ⚠️ This is a **draft PR**. Review carefully before merging.\n\n"
        f"| Field | Value |\n|---|---|\n"
        f"| Transaction ID | `{input.transaction_id}` |\n"
        f"| Process | {input.process_name} |\n"
        f"| Investigation confidence | {input.confidence} |\n"
        f"| LLM fix confidence | {fix_result.get('confidence', 'Low')} |\n\n"
        f"### Diagnosis\n{input.diagnosis}\n\n"
        f"### Recommended Action\n{input.recommended_action}\n\n"
        f"### Fix Analysis\n{fix_result.get('explanation', '')}"
        f"{diff_section}\n"
        f"---\n*Raised automatically by SpectreCodingAgent — review before merging*"
    )


if __name__ == "__main__":
    result = asyncio.run(fix(FixIn(
        transaction_id="INV-98766",
        process_name="3201 Invoice Processing",
        diagnosis="SelectorNotFoundException on 'Click Login Button' — selector <wnd app='sap.exe' title='SAP Logon' /> not found",
        recommended_action="Update selector in LoginToSAP.xaml to match current SAP window title",
        confidence="High",
    )))
    print(result)
