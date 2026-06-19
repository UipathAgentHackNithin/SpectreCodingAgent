"""
SpectreCodingAgent — orchestrates the full fix flow.

1. Find GitHub repo by process number topic
2. Check for duplicate PRs/issues
3. Fetch all XAML files via GitHub API (no clone)
4. LLM call 1: select candidate files from repo summary
5. LLM call 2: analyse candidates and produce surgical fix
6. Apply patch if possible, commit via API
7. Open DRAFT PR with labels + assignee
"""
import asyncio
import os
import time
import xml.etree.ElementTree as ET
from pydantic import BaseModel
from uipath.platform import UiPath

try:
    from .logger import get_logger
    from .auth import get_llm_token
    from .github_client import (
        find_repo_by_process, check_duplicate,
        fetch_xaml_listing, fetch_xaml_contents,
        commit_file_to_branch, ensure_branch,
        create_draft_pr, get_codeowner, _commit_report,
    )
    from .xaml_scanner import build_repo_summary
    from .llm import select_target_files, analyse_and_fix
except ImportError:
    from logger import get_logger
    from auth import get_llm_token
    from github_client import (
        find_repo_by_process, check_duplicate,
        fetch_xaml_listing, fetch_xaml_contents,
        commit_file_to_branch, ensure_branch,
        create_draft_pr, get_codeowner, _commit_report,
    )
    from xaml_scanner import build_repo_summary
    from llm import select_target_files, analyse_and_fix

log = get_logger("spectre.coding_agent")

_CG_FOLDER_PATH = "Shared/Specter"
_SUPPORT_HANDLE_FALLBACK = "<!subteam^S0BBTE9DA0N>"


def _get_support_handle(sdk: UiPath) -> str:
    try:
        asset = sdk.assets.retrieve("SPECTRE_SUPPORT_HANDLE", folder_path=_CG_FOLDER_PATH)
        value = getattr(asset, "string_value", None) or getattr(asset, "StringValue", None) or getattr(asset, "value", None)
        if value:
            return value
    except Exception as e:
        log.warning(f"Could not read SPECTRE_SUPPORT_HANDLE asset: {e}")
    return _SUPPORT_HANDLE_FALLBACK


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

    sdk = UiPath()
    support_handle = _get_support_handle(sdk)

    # ── 1. Find repo ──────────────────────────────────────────────────────────
    repo_full_name = find_repo_by_process(input.process_name)
    if not repo_full_name:
        log.warning(f"No GitHub repo found for process: {input.process_name}")
        return _empty_out(
            f"SpectreAI could not find a code repository for '{input.process_name}'. "
            f"Please contact {support_handle} for manual investigation.",
        )

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

    try:
        llm_token, base_url = get_llm_token()
    except Exception as e:
        log.error(f"LLM token acquisition failed: {e}")
        return _empty_out(
            f"SpectreAI encountered an internal error and could not run. "
            f"Please contact {support_handle} if this persists.",
        )

    # ── 3. Fetch XAML listing ─────────────────────────────────────────────────
    branch_name = f"spectre-fix/{input.transaction_id.lower()}"
    try:
        ensure_branch(repo_full_name, branch_name)
    except Exception as e:
        log.error(f"Could not create branch '{branch_name}' in '{repo_full_name}': {e}")
        return _empty_out(
            f"SpectreAI could not access the code repository for '{input.process_name}'. "
            f"Please contact {support_handle} for manual investigation.",
        )

    scan_results = fetch_xaml_listing(repo_full_name)
    repo_summary = build_repo_summary(scan_results)
    log.info(f"Fetched listing of {len(scan_results)} XAML files")

    # ── 4. LLM call 1: file selection ─────────────────────────────────────────
    selection = await select_target_files(llm_token, base_url, input.diagnosis, repo_summary)
    candidates = selection.get("candidates", [])[:3]
    selection_confidence = selection.get("confidence", "Low")
    log.info(f"File selection: candidates={candidates} confidence={selection_confidence}")

    patch_skip_reason = ""

    if not candidates:
        log.warning("LLM could not identify candidate files — opening report-only PR")
        fix_result = _no_fix_result("LLM could not identify the relevant XAML file from repo structure", "Low")
    else:
        # ── 5. Fetch candidate file contents ──────────────────────────────────
        candidate_files = fetch_xaml_contents(repo_full_name, candidates)

        if not candidate_files:
            fix_result = _no_fix_result("Candidate files identified but could not be read", "Low")
        else:
            # ── 6. LLM call 2: analyse and fix ────────────────────────────────
            try:
                fix_result = await analyse_and_fix(
                    llm_token, base_url,
                    input.diagnosis, input.recommended_action,
                    candidate_files,
                )
            except Exception as e:
                log.error(f"LLM fix analysis failed: {e}")
                fix_result = _no_fix_result(f"AI fix analysis failed — {e}", "Low")

            log.info(f"Fix analysis: can_fix={fix_result.get('can_fix')} confidence={fix_result.get('confidence')}")

            # Normalise confidence casing — LLM may return "high"/"medium"/"low"
            if "confidence" in fix_result:
                fix_result["confidence"] = fix_result["confidence"].capitalize()

            # Apply patch if possible
            if fix_result.get("can_fix") and fix_result.get("original_snippet"):
                target_file = fix_result.get("target_file", "")
                original = fix_result["original_snippet"]
                replacement = fix_result.get("replacement_snippet", "")
                original_content = candidate_files.get(target_file)

                replacement_valid = True
                try:
                    ET.fromstring(replacement)
                except ET.ParseError as xml_err:
                    replacement_valid = False
                    patch_skip_reason = f"LLM-generated replacement_snippet is not valid XML: {xml_err}"
                    log.warning(f"replacement_snippet is not valid XML — patch skipped: {xml_err}")
                    fix_result["_actually_patched"] = False
                    fix_result["target_file"] = ""
                    try:
                        _commit_report(repo_full_name, branch_name, input, fix_result)
                    except Exception as ce:
                        log.warning(f"Report commit failed (continuing to PR): {ce}")

                if replacement_valid:
                    if original_content and original in original_content:
                        patched = original_content.replace(original, replacement, 1)
                        commit_msg = fix_result.get("commit_message") or f"[SpectreAI] Fix for {input.transaction_id}"
                        commit_file_to_branch(repo_full_name, branch_name, target_file, patched, commit_msg)
                        fix_result["_actually_patched"] = True
                        log.info(f"Patch committed to {target_file} on {branch_name}")
                    else:
                        patch_skip_reason = f"original_snippet not found verbatim in `{target_file}` — possible whitespace drift or truncation"
                        fix_result["_actually_patched"] = False
                        fix_result["target_file"] = ""
                        log.warning("original_snippet not found verbatim — patch skipped")
                        try:
                            _commit_report(repo_full_name, branch_name, input, fix_result)
                        except Exception as ce:
                            log.warning(f"Report commit failed (continuing to PR): {ce}")
            else:
                try:
                    _commit_report(repo_full_name, branch_name, input, fix_result)
                except Exception as ce:
                    log.warning(f"Report commit failed (continuing to PR): {ce}")

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
    pr_body = _build_pr_body(input, fix_result, actually_patched, selection_confidence, branch_name, patch_skip_reason)
    try:
        pr_url = create_draft_pr(repo_full_name, branch_name, pr_title, pr_body, labels, assignee)
    except Exception as e:
        log.error(f"PR creation failed: {e}")
        return _empty_out(
            f"SpectreAI analysed the issue but could not submit the fix for review. "
            f"Please contact {support_handle} for manual investigation.",
        )

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


def _build_pr_body(
    input: FixIn,
    fix_result: dict,
    patched: bool,
    selection_confidence: str = "Low",
    branch_name: str = "",
    patch_skip_reason: str = "",
) -> str:
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
        skip_note = f"\n> **Patch not applied:** {patch_skip_reason}\n" if patch_skip_reason else ""
        diff_section = (
            f"\n### Proposed Change (apply manually)\n"
            f"{skip_note}"
            f"**File:** `{fix_result.get('target_file') or 'see diagnosis'}`  \n"
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
