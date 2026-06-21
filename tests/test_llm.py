"""Tests for llm module — mocked LLM service."""
import json
import pytest
from unittest.mock import MagicMock, patch, AsyncMock


def _mock_service(response_json: dict):
    """Build a mock UiPathOpenAIService that returns response_json."""
    msg = MagicMock()
    msg.content = json.dumps(response_json)
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    service = MagicMock()
    service.chat_completions = AsyncMock(return_value=response)
    return service


_PATCH_SERVICE = "spectre_coding.llm._make_service"


class TestSelectTargetFiles:
    @pytest.mark.asyncio
    async def test_returns_parsed_candidates(self):
        expected = {"candidates": ["Framework/Process.xaml"], "reason": "Activity name matches", "confidence": "High"}
        with patch(_PATCH_SERVICE, return_value=_mock_service(expected)):
            from spectre_coding.llm import select_target_files
            result = await select_target_files("tok", "http://base", "SAP login failed", "repo summary")
        assert result["candidates"] == ["Framework/Process.xaml"]
        assert result["confidence"] == "High"

    @pytest.mark.asyncio
    async def test_handles_multiple_candidates(self):
        expected = {"candidates": ["Login.xaml", "Process.xaml"], "reason": "Both relevant", "confidence": "Medium"}
        with patch(_PATCH_SERVICE, return_value=_mock_service(expected)):
            from spectre_coding.llm import select_target_files
            result = await select_target_files("tok", "http://base", "diagnosis", "summary")
        assert len(result["candidates"]) == 2

    @pytest.mark.asyncio
    async def test_raises_on_malformed_json(self):
        msg = MagicMock()
        msg.content = "not valid json at all {"
        choice = MagicMock()
        choice.message = msg
        response = MagicMock()
        response.choices = [choice]
        service = MagicMock()
        service.chat_completions = AsyncMock(return_value=response)
        with patch(_PATCH_SERVICE, return_value=service):
            from spectre_coding.llm import select_target_files
            with pytest.raises(json.JSONDecodeError):
                await select_target_files("tok", "http://base", "d", "s")


class TestAnalyseAndFix:
    @pytest.mark.asyncio
    async def test_returns_all_required_keys(self):
        expected = {
            "can_fix": True,
            "fixes": [
                {
                    "target_file": "Framework/Process.xaml",
                    "target_activity": "Click Login Button",
                    "patch_mode": "snippet",
                    "original_snippet": "<old/>",
                    "replacement_snippet": "<new/>",
                    "rewritten_xaml": "",
                    "commit_message": "Fix selector in LoginToSAP",
                }
            ],
            "explanation": "Updated selector",
            "confidence": "High",
            "issue_type_label": "SAP GUI - Broken Selector",
            "failure_category": "selector",
            "caveats": [],
        }
        with patch(_PATCH_SERVICE, return_value=_mock_service(expected)):
            from spectre_coding.llm import analyse_and_fix
            result = await analyse_and_fix("tok", "http://base", "diagnosis", "action", {"Process.xaml": "<xml/>"})
        for key in expected:
            assert key in result

    @pytest.mark.asyncio
    async def test_truncates_content_to_char_limit(self):
        captured_prompts = []
        expected = {"can_fix": False, "fixes": [], "explanation": "no fix",
                    "confidence": "Low", "issue_type_label": "Unknown App - Unknown",
                    "failure_category": "unknown"}

        service = _mock_service(expected)
        original_chat = service.chat_completions

        async def capture(*args, **kwargs):
            captured_prompts.append(args[0][-1]["content"])  # user message
            return await original_chat(*args, **kwargs)

        service.chat_completions = capture

        large_content = "X" * 40_000
        with patch(_PATCH_SERVICE, return_value=service):
            from spectre_coding.llm import analyse_and_fix
            await analyse_and_fix("tok", "http://base", "d", "r", {"big.xaml": large_content})

        assert len(captured_prompts) == 1
        # The prompt should contain at most _CONTENT_CHAR_LIMIT chars of the file content
        # Allow small overhead for the "=== FILE: big.xaml ===" header lines
        assert captured_prompts[0].count("X") <= 30_050

    @pytest.mark.asyncio
    async def test_handles_multiple_candidate_files(self):
        expected = {
            "can_fix": True,
            "fixes": [{"target_file": "Sub.xaml", "target_activity": "Invoke API",
                        "patch_mode": "full_rewrite",
                        "rewritten_xaml": "<root><Activity DisplayName='Invoke API'/></root>",
                        "original_snippet": "", "replacement_snippet": "",
                        "commit_message": "fix api call"}],
            "explanation": "fixed",
            "confidence": "Medium",
            "issue_type_label": "ServiceNow - HTTP Timeout",
            "failure_category": "api",
            "caveats": ["The API response schema may have changed"],
        }
        with patch(_PATCH_SERVICE, return_value=_mock_service(expected)):
            from spectre_coding.llm import analyse_and_fix
            result = await analyse_and_fix(
                "tok", "http://base", "d", "r",
                {"Process.xaml": "<xml1/>", "Sub.xaml": "<xml2/>"}
            )
        assert result["fixes"][0]["target_file"] == "Sub.xaml"

    @pytest.mark.asyncio
    async def test_raises_on_malformed_json(self):
        msg = MagicMock()
        msg.content = "not valid json {"
        choice = MagicMock()
        choice.message = msg
        response = MagicMock()
        response.choices = [choice]
        service = MagicMock()
        service.chat_completions = AsyncMock(return_value=response)
        with patch(_PATCH_SERVICE, return_value=service):
            from spectre_coding.llm import analyse_and_fix
            with pytest.raises(json.JSONDecodeError):
                await analyse_and_fix("tok", "http://base", "d", "r", {"f.xaml": "<xml/>"})

    @pytest.mark.asyncio
    async def test_can_fix_false_with_empty_fixes_parses_correctly(self):
        expected = {
            "can_fix": False,
            "fixes": [],
            "explanation": "Credential rotation required — cannot be fixed in XAML",
            "confidence": "High",
            "issue_type_label": "SAP GUI - Credential Expired",
            "failure_category": "credential",
            "caveats": [],
        }
        with patch(_PATCH_SERVICE, return_value=_mock_service(expected)):
            from spectre_coding.llm import analyse_and_fix
            result = await analyse_and_fix("tok", "http://base", "d", "r", {"f.xaml": "<xml/>"})
        assert result["can_fix"] is False
        assert result["fixes"] == []
        assert result["failure_category"] == "credential"

    @pytest.mark.asyncio
    async def test_caveats_list_passed_through(self):
        expected = {
            "can_fix": True,
            "fixes": [{"target_file": "f.xaml", "target_activity": "Click",
                        "patch_mode": "snippet", "original_snippet": "<old/>",
                        "replacement_snippet": "<new/>", "rewritten_xaml": "",
                        "commit_message": "fix"}],
            "explanation": "Fixed selector",
            "confidence": "High",
            "issue_type_label": "SAP GUI - Broken Selector",
            "failure_category": "selector",
            "caveats": [
                "Selector updated — verify in UI Explorer against the live SAP GUI instance",
                "Wildcard applied to title — confirm it uniquely identifies the correct element",
            ],
        }
        with patch(_PATCH_SERVICE, return_value=_mock_service(expected)):
            from spectre_coding.llm import analyse_and_fix
            result = await analyse_and_fix("tok", "http://base", "d", "r", {"f.xaml": "<xml/>"})
        assert len(result["caveats"]) == 2
        assert "UI Explorer" in result["caveats"][0]

    @pytest.mark.asyncio
    async def test_markdown_fenced_json_is_parsed(self):
        payload = {
            "can_fix": False, "fixes": [], "explanation": "no fix",
            "confidence": "Low", "issue_type_label": "Unknown App - Unknown",
            "failure_category": "unknown", "caveats": [],
        }
        msg = MagicMock()
        msg.content = f"```json\n{json.dumps(payload)}\n```"
        choice = MagicMock()
        choice.message = msg
        response = MagicMock()
        response.choices = [choice]
        service = MagicMock()
        service.chat_completions = AsyncMock(return_value=response)
        with patch(_PATCH_SERVICE, return_value=service):
            from spectre_coding.llm import analyse_and_fix
            result = await analyse_and_fix("tok", "http://base", "d", "r", {"f.xaml": "<xml/>"})
        assert result["can_fix"] is False

    @pytest.mark.asyncio
    async def test_second_file_dropped_when_combined_exceed_char_limit(self):
        captured_prompts = []
        expected = {"can_fix": False, "fixes": [], "explanation": "no fix",
                    "confidence": "Low", "issue_type_label": "Unknown App - Unknown",
                    "failure_category": "unknown", "caveats": []}

        service = _mock_service(expected)
        original_chat = service.chat_completions

        async def capture(*args, **kwargs):
            captured_prompts.append(args[0][-1]["content"])
            return await original_chat(*args, **kwargs)

        service.chat_completions = capture

        # Use unique repeated tokens unlikely to appear in the prompt template
        marker_a = "AAAA"
        marker_b = "BBBB"
        file_a = marker_a * 6_250   # 25_000 chars
        file_b = marker_b * 6_250   # 25_000 chars
        with patch(_PATCH_SERVICE, return_value=service):
            from spectre_coding.llm import analyse_and_fix
            await analyse_and_fix("tok", "http://base", "d", "r",
                                  {"first.xaml": file_a, "second.xaml": file_b})

        assert len(captured_prompts) == 1
        prompt = captured_prompts[0]
        # first file is present
        assert marker_a in prompt
        # second file is truncated — not all 25k chars present
        assert prompt.count(marker_b) < 6_250
        # combined file content does not exceed the limit (plus small header overhead)
        chars_a = prompt.count(marker_a) * len(marker_a)
        chars_b = prompt.count(marker_b) * len(marker_b)
        assert chars_a + chars_b <= 30_050

    @pytest.mark.asyncio
    async def test_prior_fix_injected_into_prompt_when_provided(self):
        captured_prompts = []
        expected = {"can_fix": False, "fixes": [], "explanation": "no fix",
                    "confidence": "Low", "issue_type_label": "Unknown App - Unknown",
                    "failure_category": "unknown", "caveats": []}

        service = _mock_service(expected)
        original_chat = service.chat_completions

        async def capture(*args, **kwargs):
            captured_prompts.append(args[0][-1]["content"])
            return await original_chat(*args, **kwargs)

        service.chat_completions = capture

        prior = "Prior fix: updated selector to use wildcard on SAP title attribute"
        with patch(_PATCH_SERVICE, return_value=service):
            from spectre_coding.llm import analyse_and_fix
            await analyse_and_fix("tok", "http://base", "d", "r",
                                  {"f.xaml": "<xml/>"}, prior_fix=prior)

        assert len(captured_prompts) == 1
        assert "PRIOR FIX FROM KNOWLEDGE BASE" in captured_prompts[0]
        assert prior in captured_prompts[0]

    @pytest.mark.asyncio
    async def test_prior_fix_not_in_prompt_when_empty(self):
        captured_prompts = []
        expected = {"can_fix": False, "fixes": [], "explanation": "no fix",
                    "confidence": "Low", "issue_type_label": "Unknown App - Unknown",
                    "failure_category": "unknown", "caveats": []}

        service = _mock_service(expected)
        original_chat = service.chat_completions

        async def capture(*args, **kwargs):
            captured_prompts.append(args[0][-1]["content"])
            return await original_chat(*args, **kwargs)

        service.chat_completions = capture

        with patch(_PATCH_SERVICE, return_value=service):
            from spectre_coding.llm import analyse_and_fix
            await analyse_and_fix("tok", "http://base", "d", "r",
                                  {"f.xaml": "<xml/>"}, prior_fix="")

        assert len(captured_prompts) == 1
        assert "PRIOR FIX FROM KNOWLEDGE BASE" not in captured_prompts[0]
