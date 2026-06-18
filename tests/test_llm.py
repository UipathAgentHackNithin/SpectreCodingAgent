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
            "target_file": "Framework/Process.xaml",
            "target_activity": "Click Login Button",
            "original_snippet": "<old/>",
            "replacement_snippet": "<new/>",
            "explanation": "Updated selector",
            "commit_message": "Fix selector in LoginToSAP",
            "confidence": "High",
            "issue_type_label": "ui-automation",
        }
        with patch(_PATCH_SERVICE, return_value=_mock_service(expected)):
            from spectre_coding.llm import analyse_and_fix
            result = await analyse_and_fix("tok", "http://base", "diagnosis", "action", {"Process.xaml": "<xml/>"})
        for key in expected:
            assert key in result

    @pytest.mark.asyncio
    async def test_truncates_content_to_char_limit(self):
        captured_prompts = []
        expected = {"can_fix": False, "target_file": "", "target_activity": "", "original_snippet": "",
                    "replacement_snippet": "", "explanation": "no fix", "commit_message": "report",
                    "confidence": "Low", "issue_type_label": "unknown"}

        service = _mock_service(expected)
        original_chat = service.chat_completions

        async def capture(*args, **kwargs):
            captured_prompts.append(args[0][-1]["content"])  # user message
            return await original_chat(*args, **kwargs)

        service.chat_completions = capture

        large_content = "X" * 20_000
        with patch(_PATCH_SERVICE, return_value=service):
            from spectre_coding.llm import analyse_and_fix
            await analyse_and_fix("tok", "http://base", "d", "r", {"big.xaml": large_content})

        assert len(captured_prompts) == 1
        # The prompt should contain at most _CONTENT_CHAR_LIMIT chars of the file content
        # Allow small overhead for the "=== FILE: big.xaml ===" header lines
        assert captured_prompts[0].count("X") <= 15_050

    @pytest.mark.asyncio
    async def test_handles_multiple_candidate_files(self):
        expected = {"can_fix": True, "target_file": "Sub.xaml", "target_activity": "Invoke API",
                    "original_snippet": "<old/>", "replacement_snippet": "<new/>",
                    "explanation": "fixed", "commit_message": "fix api call",
                    "confidence": "Medium", "issue_type_label": "api"}
        with patch(_PATCH_SERVICE, return_value=_mock_service(expected)):
            from spectre_coding.llm import analyse_and_fix
            result = await analyse_and_fix(
                "tok", "http://base", "d", "r",
                {"Process.xaml": "<xml1/>", "Sub.xaml": "<xml2/>"}
            )
        assert result["target_file"] == "Sub.xaml"
