"""
XAML fixer for SpectreCodingAgent.
Finds the primary XAML file in the cloned repo, calls the LLM for a surgical fix,
and applies the patch if the LLM returns a valid original→replacement pair.
"""
import os
from typing import Optional

try:
    from .logger import get_logger
    from .llm import analyse_and_fix
except ImportError:
    from logger import get_logger
    from llm import analyse_and_fix

log = get_logger("spectre.xaml_fixer")


def find_primary_xaml(repo_path: str) -> Optional[str]:
    """
    Return the path of the primary XAML to patch.
    Priority: Framework/Process.xaml > any Process.xaml > first .xaml found.
    """
    candidates: list[tuple[int, str]] = []  # (priority, path)
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            if not f.endswith(".xaml"):
                continue
            full = os.path.join(root, f)
            if f == "Process.xaml" and "Framework" in root:
                candidates.append((0, full))
            elif f == "Process.xaml":
                candidates.append((1, full))
            else:
                candidates.append((2, full))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


async def apply_llm_fix(
    repo_path: str,
    access_token: str,
    base_url: str,
    diagnosis: str,
    recommended_action: str,
    process_name: str,
    transaction_id: str,
) -> dict:
    """
    Find the primary XAML, ask the LLM for a surgical fix, and apply it.

    Returns:
      fixed          (bool)
      file_changed   (str)   — repo-relative path
      explanation    (str)   — what was changed or why not
      can_fix        (bool)  — LLM's assessment
      llm_confidence (str)   — High / Medium / Low
      original_snippet (str)
      replacement_snippet (str)
    """
    target = find_primary_xaml(repo_path)
    if not target:
        log.warning("No XAML file found in repo")
        return _no_fix("No XAML file found in the cloned repository", "")

    rel_path = os.path.relpath(target, repo_path)
    log.info(f"Primary XAML: {rel_path}")

    with open(target, "r", encoding="utf-8") as fh:
        xaml_content = fh.read()

    log.info(f"XAML size: {len(xaml_content)} chars — calling LLM for fix analysis")
    llm_result = await analyse_and_fix(
        access_token=access_token,
        base_url=base_url,
        xaml_content=xaml_content,
        diagnosis=diagnosis,
        recommended_action=recommended_action,
        process_name=process_name,
        transaction_id=transaction_id,
    )

    log.info(f"LLM result: can_fix={llm_result.get('can_fix')} confidence={llm_result.get('confidence')}")

    if not llm_result.get("can_fix"):
        return {
            "fixed": False,
            "file_changed": rel_path,
            "explanation": llm_result.get("reason", "LLM determined no automated fix is possible"),
            "can_fix": False,
            "llm_confidence": llm_result.get("confidence", "Low"),
            "original_snippet": "",
            "replacement_snippet": "",
        }

    original = llm_result.get("original_snippet", "")
    replacement = llm_result.get("replacement_snippet", "")

    if not original:
        return _no_fix("LLM returned can_fix=true but no original_snippet", rel_path, llm_result)

    if original not in xaml_content:
        log.warning("LLM original_snippet not found verbatim in XAML — skipping patch to avoid corruption")
        return {
            "fixed": False,
            "file_changed": rel_path,
            "explanation": f"LLM proposed a fix but the snippet could not be located verbatim in the file. Manual review needed.\n\nLLM explanation: {llm_result.get('explanation', '')}",
            "can_fix": True,
            "llm_confidence": llm_result.get("confidence", "Low"),
            "original_snippet": original,
            "replacement_snippet": replacement,
        }

    patched = xaml_content.replace(original, replacement, 1)
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(patched)

    log.info(f"Patch applied to {rel_path}")
    return {
        "fixed": True,
        "file_changed": rel_path,
        "explanation": llm_result.get("explanation", ""),
        "can_fix": True,
        "llm_confidence": llm_result.get("confidence", "Medium"),
        "original_snippet": original,
        "replacement_snippet": replacement,
    }


def _no_fix(reason: str, rel_path: str, llm_result: dict = None) -> dict:
    return {
        "fixed": False,
        "file_changed": rel_path,
        "explanation": reason,
        "can_fix": False,
        "llm_confidence": llm_result.get("confidence", "Low") if llm_result else "Low",
        "original_snippet": "",
        "replacement_snippet": "",
    }
