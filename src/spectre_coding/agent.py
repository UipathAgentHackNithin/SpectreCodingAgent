"""
SpectreCodingAgent — orchestrates the full fix flow.

1. Find GitHub repo by process number topic
2. Check for duplicate PRs/issues
3. Fetch all XAML files via GitHub API (no clone)
4. LLM call 1: select candidate files from repo summary
5. LLM call 2: analyse candidates and produce surgical fix
6. Apply patches (one per file) and commit via API
7. Open DRAFT PR with labels + assignee
"""
import asyncio
import json
import os
import re
import requests
import time
import xml.etree.ElementTree as ET
from pydantic import BaseModel
from uipath.platform import UiPath

try:
    from .logger import get_logger
    from .auth import get_llm_token, get_pat
    from .github_client import (
        find_repo_by_process, check_duplicate,
        fetch_xaml_listing, fetch_xaml_contents,
        commit_file_to_branch, ensure_branch,
        create_draft_pr, get_codeowner, get_last_committer, _commit_report,
    )
    from .xaml_scanner import build_repo_summary
    from .llm import select_target_files, analyse_and_fix
except ImportError:
    from logger import get_logger
    from auth import get_llm_token, get_pat
    from github_client import (
        find_repo_by_process, check_duplicate,
        fetch_xaml_listing, fetch_xaml_contents,
        commit_file_to_branch, ensure_branch,
        create_draft_pr, get_codeowner, get_last_committer, _commit_report,
    )
    from xaml_scanner import build_repo_summary
    from llm import select_target_files, analyse_and_fix

log = get_logger("spectre.coding_agent")

_CG_FOLDER_PATH = "Shared/Specter"
_CG_INDEX_NAME = "SpectreKB"
_CG_BUCKET_NAME = "Spectre AI"
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


def _search_kb_for_similar(sdk: UiPath, query: str) -> str:
    """Search SpectreKB for a similar past fix. Returns a summary string or empty string."""
    try:
        results = sdk.context_grounding.search(
            name=_CG_INDEX_NAME,
            query=query,
            number_of_results=1,
            folder_path=_CG_FOLDER_PATH,
        )
        if results:
            top = results[0]
            content = getattr(top, "text", None) or getattr(top, "content", None) or str(top)
            log.info(f"KB similar fix found: {content[:100]}")
            return content
    except Exception as e:
        log.warning(f"KB similarity search failed: {e}")
    return ""


def _ingest_fix_to_kb(sdk: UiPath, input: "FixIn", fix_result: dict, pr_url: str, patched: bool) -> None:
    """Upload coding agent fix outcome to SpectreKB bucket and trigger re-ingestion."""
    try:
        safe_process = input.process_name.replace(" ", "_")
        file_name = f"coding_{safe_process}_{input.transaction_id}.json"
        record = {
            "transaction_id": input.transaction_id,
            "process_name": input.process_name,
            "issue_type": fix_result.get("issue_type_label", "unknown"),
            "failure_category": fix_result.get("failure_category", "unknown"),
            "description": input.diagnosis,
            "pr_url": pr_url,
            "patch_mode": fix_result.get("patch_mode", "none"),
            "fixed": patched,
            "llm_confidence": fix_result.get("confidence", "Low"),
            "recommended_action": input.recommended_action,
        }
        sdk.buckets.upload(
            name=_CG_BUCKET_NAME,
            blob_file_path=file_name,
            content=json.dumps(record, indent=2),
            content_type="application/json",
            folder_path=_CG_FOLDER_PATH,
        )
        log.info(f"KB ingest: uploaded {file_name} to '{_CG_BUCKET_NAME}'")
        sdk.context_grounding.ingest_by_name(name=_CG_INDEX_NAME, folder_path=_CG_FOLDER_PATH)
        log.info("KB ingest: SpectreKB re-ingestion triggered")
    except Exception as e:
        log.warning(f"KB fix ingest failed (non-fatal): {e}")


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
    files_changed: list
    fix_description: str
    llm_confidence: str
    is_duplicate: bool
    message: str


def _apply_fix_entry(entry: dict, candidate_files: dict, repo_full_name: str, branch_name: str) -> tuple:
    """
    Apply a single fix entry. Returns (patched: bool, skip_reason: str).
    """
    patch_mode = entry.get("patch_mode", "snippet")
    target_file = entry.get("target_file", "")
    commit_msg = entry.get("commit_message") or "[SpectreAI] Fix"

    if patch_mode == "multi_range":
        original_content = candidate_files.get(target_file)
        if not original_content:
            return False, f"content of {target_file} not available"

        ns_decls = " ".join(re.findall(r'xmlns(?::\w+)?="[^"]*"', original_content))
        hunks = entry.get("hunks") or []
        # LLM sometimes puts the fix in top-level start_line/end_line/replacement_lines
        # instead of hunks — auto-promote to a single hunk so the patch still applies
        if not hunks and entry.get("start_line") and entry.get("end_line") and entry.get("replacement_lines"):
            hunks = [{"start_line": entry["start_line"], "end_line": entry["end_line"], "replacement_lines": entry["replacement_lines"]}]
            log.info(f"Promoted top-level line range to single hunk for {target_file}")
        if not hunks:
            return False, "multi_range fix has empty hunks list"

        # Validate all hunks before touching the file
        for i, hunk in enumerate(hunks):
            if hunk.get("start_line") is None or hunk.get("end_line") is None:
                return False, f"hunk {i} missing start_line or end_line"
            try:
                ET.fromstring(f"<_r {ns_decls}>{hunk.get('replacement_lines', '')}</_r>")
            except ET.ParseError as xml_err:
                return False, f"hunk {i} replacement_lines is not valid XML: {xml_err}"

        lines = original_content.splitlines(keepends=True)
        total = len(lines)

        # Apply hunks in reverse order so earlier line numbers stay valid
        for hunk in sorted(hunks, key=lambda h: int(h["start_line"]), reverse=True):
            s = int(hunk["start_line"]) - 1
            e = int(hunk["end_line"])
            if s < 0 or e > total or s >= e:
                return False, f"line range {hunk['start_line']}–{hunk['end_line']} out of bounds (file has {total} lines)"

            ending = "\r\n" if lines[e - 1].endswith("\r\n") else "\n" if lines[e - 1].endswith("\n") else ""
            replacement_block = hunk["replacement_lines"].rstrip("\r\n") + ending
            lines = lines[:s] + [replacement_block] + lines[e:]

        patched_content = "".join(lines)
        commit_file_to_branch(repo_full_name, branch_name, target_file, patched_content, commit_msg)
        log.info(f"multi_range patch committed to {target_file} ({len(hunks)} hunk(s))")
        return True, ""

    elif patch_mode == "full_rewrite" and entry.get("rewritten_xaml"):
        rewritten = entry["rewritten_xaml"]
        try:
            ET.fromstring(rewritten.encode("utf-8"))
        except ET.ParseError as xml_err:
            reason = f"rewritten_xaml is not well-formed XML: {xml_err}"
            log.warning(f"{target_file}: {reason}")
            return False, reason

        commit_file_to_branch(repo_full_name, branch_name, target_file, rewritten, commit_msg)
        log.info(f"Full XAML rewrite committed to {target_file} on {branch_name}")
        return True, ""

    return False, f"unsupported or incomplete patch_mode '{patch_mode}'"


def _update_env_file(new_refresh_token: str) -> None:
    """Persist the rotated refresh token back to .env so subsequent local runs don't hit invalid_grant."""
    env_path = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
    env_path = os.path.normpath(env_path)
    if not os.path.exists(env_path):
        return
    try:
        lines = open(env_path, encoding="utf-8").readlines()
        lines = [l for l in lines if not l.startswith("UIPATH_REFRESH_TOKEN=")]
        lines.append(f"UIPATH_REFRESH_TOKEN={new_refresh_token}\n")
        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
        log.info(".env updated with rotated refresh token")
    except Exception as e:
        log.warning(f"Could not update .env with rotated refresh token: {e}")


def _writeback_refresh_token(pat: str, base_url: str, new_refresh_token: str) -> None:
    """Update SPECTRE_REFRESH_TOKEN asset using the PAT directly (bypasses robot permission limits)."""
    headers = {
        "Authorization": f"Bearer {pat}",
        "Content-Type": "application/json",
        "X-UIPATH-FolderPath": _CG_FOLDER_PATH,
    }
    lookup_resp = requests.get(
        f"{base_url}/orchestrator_/odata/Assets?$filter=Name eq 'SPECTRE_REFRESH_TOKEN'",
        headers=headers,
        timeout=10,
    )
    lookup_resp.raise_for_status()
    items = lookup_resp.json().get("value", [])
    if not items:
        raise ValueError("SPECTRE_REFRESH_TOKEN asset not found in Orchestrator")
    asset_id = items[0]["Id"]
    body = {
        "Id": asset_id,
        "Name": "SPECTRE_REFRESH_TOKEN",
        "ValueType": "Credential",
        "CredentialUsername": "spectre",
        "CredentialPassword": new_refresh_token,
        "AllowDirectApiAccess": True,
    }
    put_resp = requests.put(
        f"{base_url}/orchestrator_/odata/Assets({asset_id})",
        headers=headers,
        json=body,
        timeout=10,
    )
    put_resp.raise_for_status()


def _load_credentials(sdk: UiPath) -> None:
    """Read credential assets from Orchestrator and inject into env vars if not already set."""
    for env_var, asset_name in [
        ("GITHUB_TOKEN", "GITHUB_TOKEN"),
        ("UIPATH_PAT", "SPECTRE_PAT"),
        ("UIPATH_REFRESH_TOKEN", "SPECTRE_REFRESH_TOKEN"),
    ]:
        if os.getenv(env_var):
            continue
        try:
            value = sdk.assets.retrieve_credential(asset_name, folder_path=_CG_FOLDER_PATH)
            if value:
                os.environ[env_var] = value
                log.info(f"Loaded {env_var} from Orchestrator asset {asset_name}")
        except Exception as e:
            log.warning(f"Could not load {env_var} from asset {asset_name}: {e}")


async def fix(input: FixIn) -> FixOut:
    log.info(f"SpectreCodingAgent — txn={input.transaction_id} process={input.process_name}")

    sdk = UiPath()
    _load_credentials(sdk)
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
            branch_name="", files_changed=[],
            fix_description="Duplicate — existing PR/issue found",
            llm_confidence="", is_duplicate=True,
            message=f"Duplicate found: {existing_url}",
        )

    try:
        pat, base_url = get_pat()
    except Exception as e:
        log.error(f"Orchestrator auth failed: {e}")
        return _empty_out(
            f"SpectreAI could not authenticate with Orchestrator. "
            f"Please contact {support_handle} if this persists.",
        )

    try:
        llm_token, base_url = get_llm_token()
        new_refresh_token = os.getenv("UIPATH_REFRESH_TOKEN")
        if new_refresh_token:
            try:
                _writeback_refresh_token(pat, base_url, new_refresh_token)
                log.info("Rotated refresh token written back to Orchestrator asset")
                _update_env_file(new_refresh_token)
            except Exception as wb_err:
                log.warning(f"Could not write rotated refresh token to asset: {wb_err}")
    except Exception as e:
        log.error(f"LLM token acquisition failed: {e}")
        return _empty_out(
            f"SpectreAI encountered an internal error and could not run. "
            f"Please contact {support_handle} if this persists.",
        )

    # ── 3. Fetch XAML listing ─────────────────────────────────────────────────
    safe_txn = re.sub(r"[^a-z0-9-]", "-", input.transaction_id.lower()).strip("-")
    branch_name = f"spectre-fix/{safe_txn}"
    try:
        ensure_branch(repo_full_name, branch_name)
    except Exception as e:
        log.error(f"Could not create branch '{branch_name}' in '{repo_full_name}': {e}")
        return _empty_out(
            f"SpectreAI could not access the code repository for '{input.process_name}'. "
            f"Please contact {support_handle} for manual investigation.",
        )

    prior_fix = ""
    scan_results = fetch_xaml_listing(repo_full_name)
    repo_summary = build_repo_summary(scan_results)
    log.info(f"Fetched listing of {len(scan_results)} XAML files")

    # ── 4. LLM call 1: file selection ─────────────────────────────────────────
    selection = await select_target_files(llm_token, base_url, input.diagnosis, repo_summary)
    candidates = selection.get("candidates", [])[:5]
    selection_confidence = selection.get("confidence", "Low")
    log.info(f"File selection: candidates={candidates} confidence={selection_confidence}")

    if not candidates:
        log.warning("LLM could not identify candidate files — opening report-only PR")
        fix_result = _no_fix_result("LLM could not identify the relevant XAML file from repo structure", "Low")
    else:
        # ── 5. Fetch candidate file contents ──────────────────────────────────
        candidate_files = fetch_xaml_contents(repo_full_name, candidates)

        if not candidate_files:
            fix_result = _no_fix_result("Candidate files identified but could not be read", "Low")
        else:
            # ── 6a. Search KB for prior fix to guide LLM ──────────────────────
            prior_fix = _search_kb_for_similar(sdk, input.diagnosis)

            # ── 6. LLM call 2: analyse and fix ────────────────────────────────
            try:
                fix_result = await analyse_and_fix(
                    llm_token, base_url,
                    input.diagnosis, input.recommended_action,
                    candidate_files,
                    prior_fix=prior_fix,
                )
            except Exception as e:
                log.error(f"LLM fix analysis failed: {e}")
                fix_result = _no_fix_result(f"AI fix analysis failed — {e}", "Low")

            log.info(f"Fix analysis: can_fix={fix_result.get('can_fix')} confidence={fix_result.get('confidence')}")

            if "confidence" in fix_result:
                fix_result["confidence"] = fix_result["confidence"].capitalize()

            # ── 7. Apply patches ───────────────────────────────────────────────
            if fix_result.get("can_fix"):
                fixes = fix_result.get("fixes", [])
                if not fixes:
                    log.warning("LLM returned can_fix=true but fixes=[] — treating as report-only")
                    fix_result["can_fix"] = False
                    fix_result["_patch_results"] = []
                    try:
                        _commit_report(repo_full_name, branch_name, input, fix_result)
                    except Exception as ce:
                        log.warning(f"Report commit failed (continuing to PR): {ce}")
                else:
                    # Deduplicate fixes by target_file — keep last entry per file (LLM may repeat)
                    seen = {}
                    for entry in fixes:
                        seen[entry.get("target_file", "")] = entry
                    fixes = list(seen.values())

                    patch_results = []
                    for entry in fixes:
                        target_file = entry.get("target_file", "")
                        patched, skip_reason = _apply_fix_entry(
                            entry, candidate_files, repo_full_name, branch_name
                        )
                        if not patched:
                            log.warning(f"Patch skipped for {target_file}: {skip_reason}")
                        patch_results.append({
                            "target_file": target_file,
                            "target_activity": entry.get("target_activity", ""),
                            "patch_mode": entry.get("patch_mode", ""),
                            "patched": patched,
                            "skip_reason": skip_reason,
                            "start_line": entry.get("start_line", ""),
                            "end_line": entry.get("end_line", ""),
                            "replacement_lines": entry.get("replacement_lines", ""),
                            "hunks": entry.get("hunks", []),
                            "rewritten_xaml": entry.get("rewritten_xaml", ""),
                            "commit_message": entry.get("commit_message", ""),
                        })

                    fix_result["_patch_results"] = patch_results
                    any_patched = any(r["patched"] for r in patch_results)
                    if not any_patched:
                        try:
                            _commit_report(repo_full_name, branch_name, input, fix_result)
                        except Exception as ce:
                            log.warning(f"Report commit failed (continuing to PR): {ce}")
            else:
                fix_result["_patch_results"] = []
                try:
                    _commit_report(repo_full_name, branch_name, input, fix_result)
                except Exception as ce:
                    log.warning(f"Report commit failed (continuing to PR): {ce}")

    # ── Build labels ──────────────────────────────────────────────────────────
    llm_confidence = fix_result.get("confidence", "Low")
    failure_category = fix_result.get("failure_category", "unknown")
    patch_results = fix_result.get("_patch_results", [])
    any_patched = any(r["patched"] for r in patch_results)
    labels = ["bug", "spectre-ai", failure_category]
    if llm_confidence == "Low" or not fix_result.get("can_fix"):
        labels.append("needs-human-review")

    files_changed = [r["target_file"] for r in patch_results if r["patched"]]

    # ── Assignee: last committer on primary file, fallback to CODEOWNERS ─────
    primary_file = files_changed[0] if files_changed else (candidates[0] if candidates else None)
    assignee = None
    if primary_file:
        assignee = get_last_committer(repo_full_name, primary_file)
        if assignee:
            log.info(f"Assignee from last committer on {primary_file}: {assignee}")
    if not assignee:
        assignee = get_codeowner(repo_full_name)
        if assignee:
            log.info(f"Assignee from CODEOWNERS: {assignee}")

    # ── #7: KB note for PR body — reuse prior_fix if set, else search by issue_type_label ──
    similar_fix_note = prior_fix
    if not similar_fix_note:
        issue_type_label = fix_result.get("issue_type_label", "")
        if issue_type_label and issue_type_label not in ("Unknown", ""):
            similar_fix_note = _search_kb_for_similar(sdk, issue_type_label)

    # ── Open draft PR ─────────────────────────────────────────────────────────
    pr_title = _build_pr_title(input, fix_result, any_patched)
    pr_body = _build_pr_body(input, fix_result, any_patched, selection_confidence, branch_name, similar_fix_note)
    try:
        pr_url = create_draft_pr(repo_full_name, branch_name, pr_title, pr_body, labels, assignee)
    except Exception as e:
        log.error(f"PR creation failed: {e}")
        return _empty_out(
            f"SpectreAI analysed the issue but could not submit the fix for review. "
            f"Please contact {support_handle} for manual investigation.",
        )
    log.info(f"Draft PR opened: {pr_url}")

    # ── #8: Write fix outcome to SpectreKB so future runs learn from this ────
    _ingest_fix_to_kb(sdk, input, fix_result, pr_url, any_patched)

    return FixOut(
        fixed=any_patched,
        pr_url=pr_url,
        repo_full_name=repo_full_name,
        branch_name=branch_name,
        files_changed=files_changed,
        fix_description=fix_result.get("explanation", ""),
        llm_confidence=llm_confidence,
        is_duplicate=False,
        message=f"Draft PR opened: {pr_url}" + (" (code patched)" if any_patched else " (report only)"),
    )


def _no_fix_result(reason: str, confidence: str) -> dict:
    return {
        "can_fix": False,
        "patch_mode": "none",
        "fixes": [],
        "explanation": reason,
        "commit_message": "",
        "confidence": confidence,
        "issue_type_label": "Unknown",
        "failure_category": "unknown",
        "caveats": [],
        "_patch_results": [],
    }


def _empty_out(message: str) -> FixOut:
    return FixOut(
        fixed=False, pr_url="", repo_full_name="", branch_name="",
        files_changed=[], fix_description=message,
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
    similar_fix_note: str = "",
) -> str:
    status = "Code change applied" if patched else "Report only — manual fix required"
    patch_results = fix_result.get("_patch_results", [])

    # Build per-file diff sections
    diff_section = ""
    if patch_results:
        diff_section = "\n### Changes\n"
        for r in patch_results:
            file_label = f"`{r['target_file']}`" if r["target_file"] else "see diagnosis"
            activity_label = f"`{r['target_activity']}`" if r["target_activity"] else "unknown"

            if r["patched"] and r["patch_mode"] == "full_rewrite":
                diff_section += (
                    f"\n#### {file_label} — {activity_label}\n"
                    f"> Full file rewrite committed — review the diff in this PR for exact changes.\n"
                )
            elif not r["patched"] and r["patch_mode"] == "full_rewrite" and r.get("rewritten_xaml"):
                truncated = r["rewritten_xaml"][:3000]
                suffix = "...[truncated]" if len(r["rewritten_xaml"]) > 3000 else ""
                diff_section += (
                    f"\n#### {file_label} — {activity_label} ⚠️ not applied\n"
                    f"> **Rewrite not committed:** {r['skip_reason']}\n\n"
                    f"**Proposed rewrite:**\n```xml\n{truncated}{suffix}\n```\n"
                )
            else:
                diff_section += (
                    f"\n#### {file_label} — {activity_label} ⚠️ not applied\n"
                    f"> {r['skip_reason']}\n"
                )

    caveats = fix_result.get("caveats") or []
    caveats_section = ""
    if caveats:
        items = "\n".join(f"- {c}" for c in caveats)
        caveats_section = f"\n### Developer Checks Required\n> ⚠️ SpectreAI could not verify these automatically — please review before merging:\n\n{items}\n"

    similar_section = ""
    if similar_fix_note:
        truncated = similar_fix_note[:800]
        suffix = "…" if len(similar_fix_note) > 800 else ""
        similar_section = f"\n### Similar Past Fix (from SpectreKB)\n> SpectreAI found a related fix in the knowledge base — review before merging:\n\n{truncated}{suffix}\n"

    return (
        f"## SpectreAI Coding Agent — {status}\n\n"
        f"> ⚠️ This is a **draft PR**. Review carefully before merging.\n\n"
        f"| Field | Value |\n|---|---|\n"
        f"| Transaction ID | `{input.transaction_id}` |\n"
        f"| Process | {input.process_name} |\n"
        f"| Issue | {fix_result.get('issue_type_label', 'Unknown')} |\n"
        f"| Investigation confidence | {input.confidence} |\n"
        f"| LLM fix confidence | {fix_result.get('confidence', 'Low')} |\n\n"
        f"### Diagnosis\n{input.diagnosis}\n\n"
        f"### Recommended Action\n{input.recommended_action}\n\n"
        f"### Fix Analysis\n{fix_result.get('explanation', '')}"
        f"{diff_section}"
        f"{caveats_section}"
        f"{similar_section}\n"
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
