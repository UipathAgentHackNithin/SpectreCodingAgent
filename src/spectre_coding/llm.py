"""
LLM module for SpectreCodingAgent.
Two calls:
  1. select_target_files — pick which XAML file(s) are likely responsible
  2. analyse_and_fix     — read candidate files and produce a surgical patch
"""
import json
from uipath.platform.common._config import UiPathApiConfig
from uipath.platform.common._execution_context import UiPathExecutionContext
from uipath.platform.chat._llm_gateway_service import UiPathOpenAIService, ChatModels

_CONTENT_CHAR_LIMIT = 15_000  # total chars across all candidate files sent to LLM


def _make_service(access_token: str, base_url: str) -> UiPathOpenAIService:
    execution_context = UiPathExecutionContext()
    config = UiPathApiConfig(base_url=base_url, secret=access_token, execution_context=execution_context)
    return UiPathOpenAIService(config=config, execution_context=execution_context)


def _parse(raw: str) -> dict:
    cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(cleaned)


async def select_target_files(
    access_token: str,
    base_url: str,
    diagnosis: str,
    repo_summary: str,
) -> dict:
    """
    LLM call 1: given diagnosis + repo summary, pick the most likely XAML files.

    Returns:
      candidates    (list[str])  — repo-relative paths, max 3
      reason        (str)
      confidence    (str)        — High / Medium / Low
    """
    service = _make_service(access_token, base_url)

    prompt = f"""You are a UiPath RPA expert. A bot has failed and you need to identify which XAML workflow file(s) contain the root cause.

=== DIAGNOSIS ===
{diagnosis}

=== REPOSITORY STRUCTURE ===
{repo_summary}

=== INSTRUCTIONS ===
1. Read the diagnosis carefully — look for activity names, exception types, selector strings, endpoint URLs, or variable names that appear in the repo structure.
2. Identify the 1-3 XAML files most likely to contain the failure point.
3. If you are confident about a single file, return just that one.
4. If the evidence points to multiple files (e.g. a sub-workflow called from the main process), return up to 3.
5. If you cannot determine the file with any confidence, return an empty candidates list.
6. Cap candidates at 3 maximum.

Respond ONLY in valid JSON:
{{
  "candidates": ["relative/path/to/File.xaml"],
  "reason": "one sentence explaining why these files were selected",
  "confidence": "High|Medium|Low"
}}"""

    messages = [
        {"role": "system", "content": "You are a UiPath XAML expert. Always respond with valid JSON only."},
        {"role": "user", "content": prompt},
    ]
    response = await service.chat_completions(
        messages, model=ChatModels.gpt_4_1_mini_2025_04_14, max_tokens=512, temperature=0
    )
    return _parse(response.choices[0].message.content)


async def analyse_and_fix(
    access_token: str,
    base_url: str,
    diagnosis: str,
    recommended_action: str,
    candidate_files: dict,
) -> dict:
    """
    LLM call 2: given full content of candidate files, produce a surgical fix.
    candidate_files: { "relative/path.xaml": "<xaml content>" }

    Returns:
      can_fix             (bool)
      target_file         (str)   — which file to patch
      target_activity     (str)   — DisplayName of the activity
      original_snippet    (str)   — exact XML to replace
      replacement_snippet (str)   — new XML
      explanation         (str)
      commit_message      (str)   — specific, not generic
      confidence          (str)   — High / Medium / Low
      issue_type_label    (str)   — sap / api / ui-automation / data / unknown
    """
    service = _make_service(access_token, base_url)

    # Build files section, respecting total char limit
    files_section = ""
    total = 0
    for path, content in candidate_files.items():
        remaining = _CONTENT_CHAR_LIMIT - total
        if remaining <= 0:
            break
        chunk = content[:remaining]
        files_section += f"\n=== FILE: {path} ===\n{chunk}\n"
        total += len(chunk)
        if len(content) > remaining:
            files_section += f"[truncated — {len(content) - remaining} chars omitted]\n"

    prompt = f"""You are an expert UiPath RPA developer. A bot has failed and SpectreAI has diagnosed the issue.
Your job: analyse the XAML workflow file(s) and produce a precise, surgical XML fix — or explain clearly why no automated fix is possible.

=== DIAGNOSIS ===
{diagnosis}

=== RECOMMENDED ACTION ===
{recommended_action}

=== CANDIDATE XAML FILES ==={files_section}

=== INSTRUCTIONS ===
1. Identify the exact file and activity (by DisplayName) where the failure occurs.
2. If a concrete XML change can fix it:
   - Set can_fix = true
   - original_snippet: copy the EXACT verbatim XML fragment to replace (character-for-character from above)
   - replacement_snippet: the corrected XML
   - Keep the change minimal — do not rewrite unrelated parts
   - commit_message: write a specific one-line git commit message describing the actual change (e.g. "Fix SelectorNotFoundException in LoginToSAP — update selector to match new window title")
3. If the fix requires human action (credential rotation, infra change, data fix, etc.):
   - Set can_fix = false
   - Explain exactly what a developer needs to do in reason
   - commit_message: "SpectreAI diagnosis report: <one line summary>"
4. issue_type_label: classify the root cause as one of: sap, api, ui-automation, data, unknown
5. confidence: High if you found a clear specific fragment; Medium if likely but uncertain; Low if guessing.

Respond ONLY in valid JSON:
{{
  "can_fix": true,
  "target_file": "relative/path.xaml",
  "target_activity": "DisplayName of the activity",
  "original_snippet": "",
  "replacement_snippet": "",
  "explanation": "",
  "commit_message": "",
  "confidence": "High|Medium|Low",
  "issue_type_label": "sap|api|ui-automation|data|unknown"
}}"""

    messages = [
        {"role": "system", "content": "You are a UiPath XAML expert. Always respond with valid JSON only."},
        {"role": "user", "content": prompt},
    ]
    response = await service.chat_completions(
        messages, model=ChatModels.gpt_4_1_mini_2025_04_14, max_tokens=1024, temperature=0.1
    )
    return _parse(response.choices[0].message.content)
