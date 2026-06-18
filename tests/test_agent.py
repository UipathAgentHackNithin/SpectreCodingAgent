"""Tests for the SpectreCodingAgent orchestration flow."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from spectre_coding.agent import FixIn, FixOut


FIX_IN = FixIn(
    transaction_id="INV-98766",
    process_name="3201 Invoice Processing",
    diagnosis="SAP login failed due to credential timeout",
    recommended_action="Replace hardcoded password with Get Credential from SAPCredentials asset",
    confidence="High",
)

_PATCH_FIND = "spectre_coding.agent.find_repo_by_process"
_PATCH_CLONE = "spectre_coding.agent.clone_repo"
_PATCH_PUSH = "spectre_coding.agent.push_branch"
_PATCH_PR = "spectre_coding.agent.create_pull_request"
_PATCH_FIX = "spectre_coding.agent.apply_llm_fix"
_PATCH_AUTH = "spectre_coding.agent.get_llm_token"


def _mock_auth():
    return patch(_PATCH_AUTH, return_value=("fake_token", "http://base"))


def _fix_result(fixed: bool, can_fix: bool = True, snippet: str = "OLD", replacement: str = "NEW") -> dict:
    return {
        "fixed": fixed,
        "file_changed": "Framework/Process.xaml",
        "explanation": "Replaced UNKNOWN with N/A" if fixed else "SAP credentials need rotation — manual fix",
        "can_fix": can_fix,
        "llm_confidence": "High" if fixed else "Medium",
        "original_snippet": snippet if fixed else "",
        "replacement_snippet": replacement if fixed else "",
    }


class TestAgentFlow:
    @pytest.mark.asyncio
    async def test_returns_no_pr_when_repo_not_found(self):
        with _mock_auth(), patch(_PATCH_FIND, return_value=None):
            from spectre_coding.agent import fix
            result = await fix(FIX_IN)
        assert result.fixed is False
        assert result.pr_url == ""
        assert "No GitHub repo found" in result.message

    @pytest.mark.asyncio
    async def test_opens_code_change_pr_when_fix_applied(self):
        with (
            _mock_auth(),
            patch(_PATCH_FIND, return_value="Org/InvoiceBot"),
            patch(_PATCH_CLONE),
            patch(_PATCH_FIX, AsyncMock(return_value=_fix_result(fixed=True))),
            patch(_PATCH_PUSH),
            patch(_PATCH_PR, return_value="https://github.com/Org/InvoiceBot/pull/1"),
            patch("tempfile.TemporaryDirectory") as mock_tmpdir,
        ):
            mock_tmpdir.return_value.__enter__ = MagicMock(return_value="/tmp/fake")
            mock_tmpdir.return_value.__exit__ = MagicMock(return_value=False)
            from spectre_coding.agent import fix
            result = await fix(FIX_IN)

        assert result.fixed is True
        assert result.pr_url == "https://github.com/Org/InvoiceBot/pull/1"
        assert result.repo_full_name == "Org/InvoiceBot"
        assert result.llm_confidence == "High"
        assert "code changed" in result.message

    @pytest.mark.asyncio
    async def test_opens_report_only_pr_when_llm_cannot_fix(self):
        with (
            _mock_auth(),
            patch(_PATCH_FIND, return_value="Org/InvoiceBot"),
            patch(_PATCH_CLONE),
            patch(_PATCH_FIX, AsyncMock(return_value=_fix_result(fixed=False, can_fix=False))),
            patch(_PATCH_PUSH),
            patch(_PATCH_PR, return_value="https://github.com/Org/InvoiceBot/pull/2"),
            patch("tempfile.TemporaryDirectory") as mock_tmpdir,
        ):
            mock_tmpdir.return_value.__enter__ = MagicMock(return_value="/tmp/fake")
            mock_tmpdir.return_value.__exit__ = MagicMock(return_value=False)
            from spectre_coding.agent import fix
            result = await fix(FIX_IN)

        assert result.fixed is False
        assert result.pr_url == "https://github.com/Org/InvoiceBot/pull/2"
        assert "manual fix needed" in result.message

    @pytest.mark.asyncio
    async def test_branch_name_contains_transaction_id(self):
        with (
            _mock_auth(),
            patch(_PATCH_FIND, return_value="Org/Bot"),
            patch(_PATCH_CLONE),
            patch(_PATCH_FIX, AsyncMock(return_value=_fix_result(fixed=True))),
            patch(_PATCH_PUSH) as mock_push,
            patch(_PATCH_PR, return_value="https://github.com/Org/Bot/pull/3"),
            patch("tempfile.TemporaryDirectory") as mock_tmpdir,
        ):
            mock_tmpdir.return_value.__enter__ = MagicMock(return_value="/tmp/fake")
            mock_tmpdir.return_value.__exit__ = MagicMock(return_value=False)
            from spectre_coding.agent import fix
            result = await fix(FIX_IN)

        assert "inv-98766" in result.branch_name

    @pytest.mark.asyncio
    async def test_pr_body_contains_diagnosis_and_action(self):
        pr_body_capture = {}

        def capture_pr(repo, branch, title, body, **kw):
            pr_body_capture["body"] = body
            return "https://github.com/Org/Bot/pull/4"

        with (
            _mock_auth(),
            patch(_PATCH_FIND, return_value="Org/Bot"),
            patch(_PATCH_CLONE),
            patch(_PATCH_FIX, AsyncMock(return_value=_fix_result(fixed=True))),
            patch(_PATCH_PUSH),
            patch(_PATCH_PR, side_effect=capture_pr),
            patch("tempfile.TemporaryDirectory") as mock_tmpdir,
        ):
            mock_tmpdir.return_value.__enter__ = MagicMock(return_value="/tmp/fake")
            mock_tmpdir.return_value.__exit__ = MagicMock(return_value=False)
            from spectre_coding.agent import fix
            await fix(FIX_IN)

        body = pr_body_capture["body"]
        assert "SAP login failed" in body
        assert "SAPCredentials" in body
        assert "INV-98766" in body
        assert "SpectreAI" in body


class TestPrBodyBuilders:
    def test_code_change_pr_includes_xml_diff(self):
        from spectre_coding.agent import _build_pr_body
        fix_result = _fix_result(fixed=True, snippet="<Assign Value='old'/>", replacement="<GetCredential/>")
        body = _build_pr_body(FIX_IN, fix_result)
        assert "```xml" in body
        assert "<Assign Value='old'/>" in body
        assert "<GetCredential/>" in body

    def test_report_only_pr_does_not_show_applied_change(self):
        from spectre_coding.agent import _build_pr_body
        fix_result = _fix_result(fixed=False, can_fix=False)
        body = _build_pr_body(FIX_IN, fix_result)
        assert "Code change applied" not in body
        assert "manual fix required" in body.lower() or "report only" in body.lower()

    def test_proposed_change_shown_when_snippet_not_located(self):
        from spectre_coding.agent import _build_pr_body
        fix_result = {
            "fixed": False,
            "can_fix": True,
            "file_changed": "Framework/Process.xaml",
            "explanation": "LLM proposed fix but snippet not found verbatim",
            "llm_confidence": "Medium",
            "original_snippet": "<SomeOldXml/>",
            "replacement_snippet": "<SomeNewXml/>",
        }
        body = _build_pr_body(FIX_IN, fix_result)
        assert "Proposed Change" in body
        assert "<SomeOldXml/>" in body
        assert "<SomeNewXml/>" in body
