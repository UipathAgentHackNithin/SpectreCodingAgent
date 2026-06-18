"""
Analyzes a bot repo and applies XAML fixes based on the diagnosis from SpectreAI.
Searches for the most relevant XAML file and patches error messages / exception text.
"""
import os
import re
from typing import Optional

try:
    from .logger import get_logger
except ImportError:
    from logger import get_logger

log = get_logger("spectre.xaml_fixer")


def find_xaml_file(repo_path: str, process_name: str) -> Optional[str]:
    """Walk repo looking for Framework/Process.xaml as the primary target."""
    candidates = []
    for root, dirs, files in os.walk(repo_path):
        # Skip hidden dirs
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            if f.endswith(".xaml"):
                full = os.path.join(root, f)
                if "Process.xaml" in f and "Framework" in root:
                    return full
                candidates.append(full)
    return candidates[0] if candidates else None


def apply_fix(
    repo_path: str,
    diagnosis: str,
    recommended_action: str,
    process_name: str,
    transaction_id: str,
) -> dict:
    """
    Apply a code fix to the repo based on the diagnosis.
    Returns dict with: fixed (bool), file_changed (str), description (str).
    """
    target_file = find_xaml_file(repo_path, process_name)
    if not target_file:
        log.warning("No XAML file found to patch")
        return {"fixed": False, "file_changed": "", "description": "No XAML file found"}

    rel_path = os.path.relpath(target_file, repo_path)
    log.info(f"Target XAML: {rel_path}")

    with open(target_file, "r", encoding="utf-8") as fh:
        content = fh.read()

    original = content
    fix_description = ""

    # Fix 1: SAP login errors — add retry logic marker in log message
    if "sap" in diagnosis.lower() and ("login" in diagnosis.lower() or "authentication" in diagnosis.lower()):
        content, count = _patch_log_message(
            content,
            old_pattern=r"(Failed to process|System error on)",
            new_prefix="[SAP_RETRY] ",
        )
        if count:
            fix_description = f"Added SAP retry marker to {count} log message(s) — SAP login instability detected"
        else:
            fix_description = "SAP login issue detected — manual review of SAP credentials/connectivity required"

    # Fix 2: Timeout errors — add timeout note
    elif "timeout" in diagnosis.lower():
        content, count = _patch_log_message(
            content,
            old_pattern=r"(Failed to process|System error on)",
            new_prefix="[TIMEOUT] ",
        )
        fix_description = f"Added timeout marker to {count} log message(s) — consider increasing wait timeouts"

    # Fix 3: Business rule / validation errors — improve error message clarity
    elif "business rule" in diagnosis.lower() or "validation" in diagnosis.lower():
        content, count = _patch_throw_message(
            content,
            old_fragment="UNKNOWN",
            new_fragment="N/A",
        )
        if count:
            fix_description = f"Replaced {count} UNKNOWN fallback(s) with N/A for cleaner business exception messages"
        else:
            fix_description = "Business rule issue — no automatic patch applied; manual review recommended"

    # Generic: annotate recommended action into first log message
    else:
        content, count = _inject_recommended_action(content, recommended_action, transaction_id)
        fix_description = f"Injected recommended action into log context ({count} message(s) updated)"

    if content == original:
        log.info("No changes made — fix is informational only")
        return {"fixed": False, "file_changed": rel_path, "description": fix_description}

    with open(target_file, "w", encoding="utf-8") as fh:
        fh.write(content)

    log.info(f"Fix applied: {fix_description}")
    return {"fixed": True, "file_changed": rel_path, "description": fix_description}


def _patch_log_message(content: str, old_pattern: str, new_prefix: str) -> tuple:
    pattern = re.compile(
        r'(Message="\[)(' + old_pattern.lstrip("(").rstrip(")") + r')',
        re.IGNORECASE,
    )
    new_content, count = pattern.subn(r'\1' + new_prefix + r'\2', content)
    return new_content, count


def _patch_throw_message(content: str, old_fragment: str, new_fragment: str) -> tuple:
    new_content = content.replace(old_fragment, new_fragment)
    count = content.count(old_fragment)
    return new_content, count


def _inject_recommended_action(content: str, recommended_action: str, transaction_id: str) -> tuple:
    safe_action = recommended_action.replace('"', "'").replace("<", "").replace(">", "")[:120]
    pattern = re.compile(r'(DisplayName="Log Message Process Start"[^/]*/>\s*)', re.DOTALL)
    injection = f'<ui:LogMessage DisplayName="Spectre Fix Note" Level="Warn" Message="[&quot;[SPECTRE] {transaction_id}: {safe_action}&quot;]" />\n    '
    new_content, count = pattern.subn(r'\1' + injection, content, count=1)
    return new_content, count
