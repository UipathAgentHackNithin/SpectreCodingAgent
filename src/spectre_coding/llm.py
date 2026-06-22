"""
LLM module for SpectreCodingAgent.
Two calls:
  1. select_target_files — pick which XAML file(s) are likely responsible
  2. analyse_and_fix     — read candidate files and produce a surgical patch
"""
import json
from uipath.platform.common._config import UiPathApiConfig
from .xaml_rules import FIXABLE_PATTERNS, FULL_REWRITE_RULES, SNIPPET_RULES
from uipath.platform.common._execution_context import UiPathExecutionContext
from uipath.platform.chat._llm_gateway_service import UiPathOpenAIService, ChatModels

_CONTENT_CHAR_LIMIT = 30_000  # total chars across all candidate files sent to LLM (~7500 tokens input)


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
2. Identify ALL XAML files that are likely involved in the failure — this includes:
   - The file where the exception was thrown
   - Any sub-workflows invoked from that file that may contain the root cause
   - Shared utility workflows referenced by the failing activity
3. If you are confident about a single file, return just that one.
4. Return only files that are genuinely relevant — do not pad the list with unlikely candidates.
5. If you cannot determine the file with any confidence, return an empty candidates list.
6. Cap candidates at 5 maximum — content will be fetched for all returned files so only include files worth reading.

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
        messages, model=ChatModels.gpt_4_1_mini_2025_04_14, max_tokens=1024, temperature=0
    )
    return _parse(response.choices[0].message.content)


async def analyse_and_fix(
    access_token: str,
    base_url: str,
    diagnosis: str,
    recommended_action: str,
    candidate_files: dict,
    prior_fix: str = "",
) -> dict:
    """
    LLM call 2: given full content of candidate files, produce the minimal fix.
    candidate_files: { "relative/path.xaml": "<xaml content>" }

    Returns:
      can_fix             (bool)
      fixes               (list)  — one entry per file to patch, each with:
                                      target_file, target_activity, patch_mode,
                                      start_line, end_line, replacement_lines, rewritten_xaml,
                                      commit_message
      explanation         (str)
      confidence          (str)   — High / Medium / Low
      issue_type_label    (str)   — human-readable app + failure description e.g. "SAP GUI - Broken Selector"
      failure_category    (str)   — selector / credential / data / api / config / orchestrator / exception-handling / unknown
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
        # Prepend 1-based line numbers so the LLM can report exact start_line/end_line
        numbered = "\n".join(f"{i+1}: {line}" for i, line in enumerate(chunk.splitlines()))
        files_section += f"\n=== FILE: {path} ===\n{numbered}\n"
        total += len(chunk)
        if len(content) > remaining:
            files_section += f"[truncated — {len(content) - remaining} chars omitted]\n"

    prior_fix_section = ""
    if prior_fix:
        prior_fix_section = f"\n=== PRIOR FIX FROM KNOWLEDGE BASE ===\nA similar issue was fixed before. Use this as a reference — adapt the approach to the current XAML, do not copy blindly:\n{prior_fix[:1500]}\n"

    prompt = f"""You are an expert UiPath RPA developer. A bot has failed and SpectreAI has diagnosed the issue.
Your job: analyse the XAML workflow file(s) and produce the minimal fix — or explain clearly why no automated fix is possible.

=== DIAGNOSIS ===
{diagnosis}

=== RECOMMENDED ACTION ===
{recommended_action}{prior_fix_section}
=== CANDIDATE XAML FILES ==={files_section}

=== INSTRUCTIONS ===
1. Identify ALL files and activities involved in the failure — a fix may span multiple files.
2. If a concrete XML change can fix it (set can_fix = true), produce one entry per file in the fixes array.

   STEP A — choose patch_mode:
     - any targeted line change (1 or more blocks) → patch_mode = "multi_range"
     - structural change (add/remove/move activity) → patch_mode = "full_rewrite"
   This decision must be made FIRST before writing any replacement content.

   For each fix entry choose the patch_mode determined above:

   patch_mode = "multi_range"  — use for ALL targeted fixes (single or multiple blocks):
     - Selector string / attribute value update
     - Single or multiple property changes
     - Two or more independent activities each needing a different fix
     Rules:
       - hunks: array of objects, one per changed block, each with:
           start_line (int), end_line (int), replacement_lines (string)
           - start_line/end_line: 1-based line numbers (inclusive). Use the "N: " line numbers shown above.
             Widen the range to cover the full XML element (include opening and closing tags).
             Never split an element across the boundary.
           - replacement_lines: the COMPLETE corrected XML replacing that range.
             Include EVERY line in the range, fully rewritten. Do NOT omit unchanged lines.
             Must be a valid XML fragment on its own.
       - Hunks MUST NOT overlap. Order them however you like — they are applied bottom-to-top automatically.
       - start_line/end_line/replacement_lines at the top level: set to null/"" (use hunks instead)
       - rewritten_xaml: "" (empty)

   patch_mode = "full_rewrite"  — use ONLY when the fix cannot be expressed as line ranges:
     - Adding or removing an activity
     - Restructuring control flow (If/Sequence/TryCatch blocks)
     - Moving an activity to a different position
     Rules:
       - rewritten_xaml: the COMPLETE corrected file content (full XML, first line to last)
       - start_line: null, end_line: null, replacement_lines: "", hunks: []
       {FULL_REWRITE_RULES}


   For each fix entry:
     - commit_message: specific one-line git commit message describing that file's change

   {FIXABLE_PATTERNS}

2a. caveats: even when can_fix = true, populate this list with things you SUSPECT may also be wrong
    but cannot verify or fix from XAML alone. These are shown to the developer as warnings. Examples:
     - API: "The API response schema may have changed — verify the endpoint contract matches what the bot expects"
     - API: "Auth token scope or expiry may need checking — SpectreAI cannot verify live credentials"
     - API: "Endpoint URL was fixed but the API may have additional breaking changes not visible in XAML"
     - DB: "Column name was fixed but verify the full table schema matches what the query expects"
     - DB: "Connection string is stored in in_Config — verify it points to the correct environment"
     - DB: "Query result null-check added but the root cause may be missing data in the source table"
     - General: "Fix addresses the immediate failure but the underlying root cause may recur"
    Leave caveats as [] if you have no meaningful warnings to add.

3. Set can_fix = false, fixes = [] when the fix cannot be expressed as a XAML change, including:
   - Credential / password rotation or re-entry (cannot be hardcoded in XAML)
   - Infrastructure changes (firewall rules, network access, server availability, certificates)
   - Environment configuration (missing Orchestrator asset, queue, or process not deployed)
   - Data issues (corrupt input file, empty queue, missing database record, wrong data format at source)
   - Third-party API outage or endpoint deprecation requiring vendor action
   - UiPath Studio / Robot version incompatibility requiring upgrade
   - licence or permission issues (robot not licensed, user account locked, AD group missing)
   - The root cause is unclear from the available XAML and logs — do not guess a fix with Low confidence
   - The change required is too risky to automate (e.g. restructuring a core framework file shared across many processes)
   In all these cases explain exactly what a developer or admin needs to do in the explanation field.

4. issue_type_label: MUST follow the exact format "<App> - <Failure Type>" (title case, hyphen separator).
   Step 1 — identify <App> from the XAML:
     - app='saplogon.exe' or wnd[ selectors                              → "SAP GUI"
     - app='chrome.exe' or app='msedge.exe' + url contains servicenow.com → "ServiceNow"
     - app='chrome.exe' + url contains salesforce.com                   → "Salesforce"
     - app='chrome.exe' + url contains workday.com                      → "Workday"
     - app='chrome.exe' + url contains successfactors.com               → "SuccessFactors"
     - app='OUTLOOK.EXE' or UiPath.Mail.Activities namespace            → "Outlook"
     - UiPath.Excel.Activities namespace or app='EXCEL.EXE'            → "Excel"
     - UiPath.PDF.Activities namespace                                  → "PDF"
     - UiPath.Database.Activities namespace                             → "Database"
     - app='WINWORD.EXE'                                                → "Word"
     - app='chrome.exe' or app='msedge.exe' with unknown url            → "Browser"
     - selectors stored in in_Config (not visible) → infer from diagnosis text; only use "Unknown App"
       if the diagnosis gives absolutely no clue about the application

   Step 2 — identify <Failure Type> from the failure:
     - Selector not found / element changed / stale element → "Broken Selector"
     - Login failed / token expired / account locked        → "Credential Expired"
     - HTTP 4xx/5xx / connection refused / DNS failure      → "HTTP Error"
     - HTTP timeout / connection timeout                    → "HTTP Timeout"
     - Null or empty DataTable / missing input data         → "Null Data"
     - File not found / IO exception on file read/write     → "File Not Found"
     - Wrong config value / missing Orchestrator asset      → "Config Error"
     - Unhandled exception / missing null-check             → "Unhandled Exception"
     - Missing null-check on query result                   → "Null Query Result"
     - Wrong SQL syntax / wrong column name                 → "Query Error"

   Examples of correctly formatted values:
     "SAP GUI - Broken Selector", "ServiceNow - HTTP Timeout", "Salesforce - HTTP Error",
     "Excel - Null Data", "Outlook - Credential Expired", "Browser - Broken Selector",
     "Database - Query Error", "PDF - File Not Found", "Workday - Config Error"

5. failure_category: classify into exactly one of these fixed values:
   selector / credential / data / api / config / orchestrator / exception-handling / unknown

   Mapping guide — use this to pick the right category:
     selector          — element not found, SelectorNotFoundException, UI element changed, broken wnd/aaname/css selector,
                         stale element, timeout waiting for element to appear, UI application layout changed
     credential        — login failed, wrong password, account locked, token expired, certificate error,
                         authentication failure, 2FA / MFA prompt appeared
     data              — null or empty DataTable, missing input file, file not found, wrong file path,
                         corrupt or unexpected data format, empty queue, missing DB record,
                         network/shared-drive path unavailable, IO exception on file read/write
     api               — HTTP error (4xx/5xx), connection refused, DNS failure, SSL/TLS error,
                         timeout on HTTP call, wrong endpoint/method/header/parameter,
                         unexpected API response structure, REST/SOAP call failed
     config            — missing or wrong Orchestrator asset value, wrong config key in in_Config,
                         environment mismatch (DEV/UAT/PROD), wrong queue or process name in config,
                         missing environment variable
     orchestrator      — job failed to start, robot not licensed, process not deployed,
                         queue does not exist, asset not found in Orchestrator folder,
                         Orchestrator connectivity issue
     exception-handling — unhandled exception in TryCatch, missing null-check, missing retry logic,
                          BusinessRuleException or ApplicationException thrown without proper handling,
                          error propagated instead of caught
     unknown           — use ONLY when none of the above fit and you cannot determine the category

6. confidence: High if you found a clear specific change; Medium if likely but uncertain; Low if guessing.

Respond ONLY in valid JSON:
{{
  "can_fix": true,
  "fixes": [
    {{
      "target_file": "relative/path.xaml",
      "target_activity": "DisplayName of the activity",
      "patch_mode": "multi_range|full_rewrite",
      "start_line": null,
      "end_line": null,
      "replacement_lines": "",
      "hunks": [],
      "rewritten_xaml": "",
      "commit_message": ""
    }}
  ],
  "explanation": "",
  "confidence": "High|Medium|Low",
  "issue_type_label": "<App> - <Failure Type>",
  "failure_category": "selector|credential|data|api|config|orchestrator|exception-handling|unknown",
  "caveats": []
}}"""

    messages = [
        {"role": "system", "content": "You are a UiPath XAML expert. Always respond with valid JSON only."},
        {"role": "user", "content": prompt},
    ]
    response = await service.chat_completions(
        messages, model=ChatModels.gpt_4_1_mini_2025_04_14, max_tokens=16384, temperature=0.1
    )
    return _parse(response.choices[0].message.content)
