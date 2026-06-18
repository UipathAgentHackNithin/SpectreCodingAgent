"""
LLM module for SpectreCodingAgent.
Uses UiPath LLM gateway to analyse a XAML file and produce a surgical code fix.
"""
import json
from uipath.platform.common._config import UiPathApiConfig
from uipath.platform.common._execution_context import UiPathExecutionContext
from uipath.platform.chat._llm_gateway_service import UiPathOpenAIService, ChatModels

# Max characters of XAML to send — keeps the prompt under token limits.
_XAML_CHAR_LIMIT = 12_000


def _make_service(access_token: str, base_url: str) -> UiPathOpenAIService:
    execution_context = UiPathExecutionContext()
    config = UiPathApiConfig(base_url=base_url, secret=access_token, execution_context=execution_context)
    return UiPathOpenAIService(config=config, execution_context=execution_context)


async def analyse_and_fix(
    access_token: str,
    base_url: str,
    xaml_content: str,
    diagnosis: str,
    recommended_action: str,
    process_name: str,
    transaction_id: str,
) -> dict:
    """
    Ask the LLM to analyse the XAML and return a precise, surgical fix.

    Returns a dict with:
      can_fix        (bool)   — whether a concrete change is possible
      reason         (str)    — why fix is or isn't possible
      original_snippet (str)  — exact XML fragment to replace (empty if can_fix=False)
      replacement_snippet (str) — new XML fragment (empty if can_fix=False)
      explanation    (str)    — human-readable description of the change
      confidence     (str)    — High / Medium / Low
    """
    service = _make_service(access_token, base_url)

    xaml_excerpt = xaml_content[:_XAML_CHAR_LIMIT]
    truncated_note = f"\n[NOTE: XAML truncated to {_XAML_CHAR_LIMIT} chars for context window]" if len(xaml_content) > _XAML_CHAR_LIMIT else ""

    prompt = f"""You are an expert UiPath RPA developer. A bot has failed and SpectreAI has diagnosed the issue.
Your job: analyse the XAML workflow and produce a precise, surgical XML fix — or explain why no automated fix is possible.

=== INCIDENT ===
Transaction ID : {transaction_id}
Process        : {process_name}
Diagnosis      : {diagnosis}
Recommended action: {recommended_action}

=== XAML WORKFLOW ==={truncated_note}
{xaml_excerpt}

=== INSTRUCTIONS ===
1. Read the XAML carefully in the context of the diagnosis.
2. Identify the exact XML fragment that needs to change (if any).
3. If a concrete change can be made (e.g. wrong asset name, hardcoded value, missing null check, wrong exception type):
   - Set can_fix = true
   - Provide original_snippet as the EXACT verbatim XML text to replace (copy it character-for-character from the XAML above)
   - Provide replacement_snippet as the corrected XML
   - Keep the change minimal and surgical — do not rewrite unrelated parts
4. If the fix requires human judgement, new activities not in the file, or external system changes:
   - Set can_fix = false
   - Explain clearly in reason what a developer needs to do manually
5. confidence: High if you found a clear, specific fragment to change; Medium if likely but uncertain; Low if guessing.

Respond ONLY in valid JSON with these exact keys:
{{
  "can_fix": <true|false>,
  "reason": "<string>",
  "original_snippet": "<string — exact XML from the XAML, empty string if can_fix=false>",
  "replacement_snippet": "<string — new XML, empty string if can_fix=false>",
  "explanation": "<string — what this change does and why>",
  "confidence": "<High|Medium|Low>"
}}"""

    messages = [
        {"role": "system", "content": "You are a UiPath XAML expert. Always respond with valid JSON only."},
        {"role": "user", "content": prompt},
    ]
    response = await service.chat_completions(
        messages,
        model=ChatModels.gpt_4_1_mini_2025_04_14,
        max_tokens=1024,
        temperature=0.1,
    )
    raw = (
        response.choices[0].message.content
        .strip()
        .removeprefix("```json")
        .removeprefix("```")
        .removesuffix("```")
        .strip()
    )
    return json.loads(raw)
