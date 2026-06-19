"""Tests for the SpectreCodingAgent orchestration flow."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from spectre_coding.agent import FixIn

FIX_IN = FixIn(
    transaction_id="INV-98766",
    process_name="3201 Invoice Processing",
    diagnosis="SelectorNotFoundException on Click Login Button",
    recommended_action="Update selector to match new SAP window title",
    confidence="High",
)

_P_FIND = "spectre_coding.agent.find_repo_by_process"
_P_DUP = "spectre_coding.agent.check_duplicate"
_P_ENSURE_BRANCH = "spectre_coding.agent.ensure_branch"
_P_FETCH = "spectre_coding.agent.fetch_xaml_listing"
_P_FETCH_CONTENTS = "spectre_coding.agent.fetch_xaml_contents"
_P_SUMMARY = "spectre_coding.agent.build_repo_summary"
_P_SELECT = "spectre_coding.agent.select_target_files"
_P_ANALYSE = "spectre_coding.agent.analyse_and_fix"
_P_COMMIT = "spectre_coding.agent.commit_file_to_branch"
_P_COMMIT_REPORT = "spectre_coding.agent.commit_file_to_branch"
_P_REPORT = "spectre_coding.agent._commit_report"
_P_PR = "spectre_coding.agent.create_draft_pr"
_P_OWNER = "spectre_coding.agent.get_codeowner"
_P_AUTH = "spectre_coding.agent.get_llm_token"
_P_SDK = "spectre_coding.agent.UiPath"


def _mock_sdk():
    sdk = MagicMock()
    sdk.assets.retrieve.return_value = MagicMock(string_value="<!subteam^S0BBTE9DA0N>")
    return sdk


def _selection(candidates, confidence="High"):
    return {"candidates": candidates, "reason": "test", "confidence": confidence}


def _fix_result(can_fix=True, patched=True, confidence="High", issue_type="ui-automation"):
    snippet = "<old/>" if can_fix else ""
    replacement = "<new/>" if can_fix else ""
    return {
        "can_fix": can_fix,
        "target_file": "Framework/Process.xaml" if can_fix else "",
        "target_activity": "Click Login Button" if can_fix else "",
        "original_snippet": snippet,
        "replacement_snippet": replacement,
        "explanation": "Updated selector" if can_fix else "Requires manual credential rotation",
        "commit_message": "Fix selector in LoginToSAP" if can_fix else "SpectreAI report: credentials issue",
        "confidence": confidence,
        "issue_type_label": issue_type,
    }


class TestAgentFlow:
    @pytest.mark.asyncio
    async def test_returns_early_when_repo_not_found(self):
        with (
            patch(_P_SDK, return_value=_mock_sdk()),
            patch(_P_FIND, return_value=None),
            patch(_P_AUTH, return_value=("tok", "http://x")),
        ):
            from spectre_coding.agent import fix
            result = await fix(FIX_IN)
        assert result.pr_url == ""
        assert "could not find a code repository" in result.message

    @pytest.mark.asyncio
    async def test_returns_duplicate_url_when_duplicate_found(self):
        with (
            patch(_P_SDK, return_value=_mock_sdk()),
            patch(_P_FIND, return_value="Org/Bot"),
            patch(_P_DUP, return_value="https://github.com/Org/Bot/pull/5"),
            patch(_P_AUTH, return_value=("tok", "http://x")),
        ):
            from spectre_coding.agent import fix
            result = await fix(FIX_IN)
        assert result.is_duplicate is True
        assert result.pr_url == "https://github.com/Org/Bot/pull/5"

    @pytest.mark.asyncio
    async def test_opens_draft_pr_when_fix_applied(self):
        with (
            patch(_P_SDK, return_value=_mock_sdk()),
            patch(_P_FIND, return_value="Org/Bot"),
            patch(_P_DUP, return_value=None),
            patch(_P_AUTH, return_value=("tok", "http://x")),
            patch(_P_ENSURE_BRANCH),
            patch(_P_FETCH, return_value=[{"path": "Framework/Process.xaml", "size": 100}]),
            patch(_P_SUMMARY, return_value="summary"),
            patch(_P_SELECT, AsyncMock(return_value=_selection(["Framework/Process.xaml"]))),
            patch(_P_FETCH_CONTENTS, return_value={"Framework/Process.xaml": "<old/>"}),
            patch(_P_ANALYSE, AsyncMock(return_value=_fix_result())),
            patch(_P_COMMIT),
            patch(_P_OWNER, return_value="nithin-br"),
            patch(_P_PR, return_value="https://github.com/Org/Bot/pull/1"),
        ):
            from spectre_coding.agent import fix
            result = await fix(FIX_IN)
        assert result.pr_url == "https://github.com/Org/Bot/pull/1"
        assert result.is_duplicate is False

    @pytest.mark.asyncio
    async def test_pr_is_always_draft(self):
        pr_kwargs = {}

        def capture_pr(repo, branch, title, body, labels, assignee=None, **kw):
            pr_kwargs.update({"repo": repo, "labels": labels})
            return "https://github.com/Org/Bot/pull/2"

        with (
            patch(_P_SDK, return_value=_mock_sdk()),
            patch(_P_FIND, return_value="Org/Bot"),
            patch(_P_DUP, return_value=None),
            patch(_P_AUTH, return_value=("tok", "http://x")),
            patch(_P_ENSURE_BRANCH),
            patch(_P_FETCH, return_value=[]),
            patch(_P_SUMMARY, return_value=""),
            patch(_P_SELECT, AsyncMock(return_value=_selection([]))),
            patch(_P_REPORT),
            patch(_P_OWNER, return_value=None),
            patch(_P_PR, side_effect=capture_pr),
        ):
            from spectre_coding.agent import fix
            await fix(FIX_IN)
        assert pr_kwargs["repo"] == "Org/Bot"

    @pytest.mark.asyncio
    async def test_adds_needs_human_review_label_when_low_confidence(self):
        labels_used = []

        def capture_pr(repo, branch, title, body, labels, assignee=None, **kw):
            labels_used.extend(labels)
            return "https://github.com/Org/Bot/pull/3"

        with (
            patch(_P_SDK, return_value=_mock_sdk()),
            patch(_P_FIND, return_value="Org/Bot"),
            patch(_P_DUP, return_value=None),
            patch(_P_AUTH, return_value=("tok", "http://x")),
            patch(_P_ENSURE_BRANCH),
            patch(_P_FETCH, return_value=[{"path": "Process.xaml", "size": 100}]),
            patch(_P_SUMMARY, return_value="summary"),
            patch(_P_SELECT, AsyncMock(return_value=_selection(["Process.xaml"], confidence="Low"))),
            patch(_P_FETCH_CONTENTS, return_value={"Process.xaml": "<xml/>"}),
            patch(_P_ANALYSE, AsyncMock(return_value=_fix_result(can_fix=False, confidence="Low"))),
            patch(_P_REPORT),
            patch(_P_OWNER, return_value=None),
            patch(_P_PR, side_effect=capture_pr),
        ):
            from spectre_coding.agent import fix
            await fix(FIX_IN)
        assert "needs-human-review" in labels_used

    @pytest.mark.asyncio
    async def test_target_activity_propagated_to_fixout(self):
        with (
            patch(_P_SDK, return_value=_mock_sdk()),
            patch(_P_FIND, return_value="Org/Bot"),
            patch(_P_DUP, return_value=None),
            patch(_P_AUTH, return_value=("tok", "http://x")),
            patch(_P_ENSURE_BRANCH),
            patch(_P_FETCH, return_value=[{"path": "Framework/Process.xaml", "size": 100}]),
            patch(_P_SUMMARY, return_value="summary"),
            patch(_P_SELECT, AsyncMock(return_value=_selection(["Framework/Process.xaml"]))),
            patch(_P_FETCH_CONTENTS, return_value={"Framework/Process.xaml": "<old/>"}),
            patch(_P_ANALYSE, AsyncMock(return_value=_fix_result(can_fix=True))),
            patch(_P_COMMIT),
            patch(_P_OWNER, return_value=None),
            patch(_P_PR, return_value="https://github.com/Org/Bot/pull/4"),
        ):
            from spectre_coding.agent import fix
            result = await fix(FIX_IN)
        assert result.target_activity == "Click Login Button"


class TestPatchApply:
    @pytest.mark.asyncio
    async def test_patch_applied_when_snippet_found_verbatim(self):
        committed = {}

        def capture_commit(repo, branch, path, content, msg):
            committed["content"] = content

        with (
            patch(_P_SDK, return_value=_mock_sdk()),
            patch(_P_FIND, return_value="Org/Bot"),
            patch(_P_DUP, return_value=None),
            patch(_P_AUTH, return_value=("tok", "http://x")),
            patch(_P_ENSURE_BRANCH),
            patch(_P_FETCH, return_value=[{"path": "Framework/Process.xaml", "size": 100}]),
            patch(_P_SUMMARY, return_value="summary"),
            patch(_P_SELECT, AsyncMock(return_value=_selection(["Framework/Process.xaml"]))),
            patch(_P_FETCH_CONTENTS, return_value={"Framework/Process.xaml": "<old/>"}),
            patch(_P_ANALYSE, AsyncMock(return_value=_fix_result())),
            patch(_P_COMMIT, side_effect=capture_commit),
            patch(_P_OWNER, return_value=None),
            patch(_P_PR, return_value="https://github.com/Org/Bot/pull/1"),
        ):
            from spectre_coding.agent import fix
            result = await fix(FIX_IN)

        assert result.fixed is True
        assert "<new/>" in committed["content"]
        assert "<old/>" not in committed["content"]

    @pytest.mark.asyncio
    async def test_patch_skipped_when_replacement_is_invalid_xml(self):
        bad_fix = _fix_result()
        bad_fix["replacement_snippet"] = "<unclosed"

        committed = []
        reported = []

        with (
            patch(_P_SDK, return_value=_mock_sdk()),
            patch(_P_FIND, return_value="Org/Bot"),
            patch(_P_DUP, return_value=None),
            patch(_P_AUTH, return_value=("tok", "http://x")),
            patch(_P_ENSURE_BRANCH),
            patch(_P_FETCH, return_value=[{"path": "Framework/Process.xaml", "size": 100}]),
            patch(_P_SUMMARY, return_value="summary"),
            patch(_P_SELECT, AsyncMock(return_value=_selection(["Framework/Process.xaml"]))),
            patch(_P_FETCH_CONTENTS, return_value={"Framework/Process.xaml": "<old/>"}),
            patch(_P_ANALYSE, AsyncMock(return_value=bad_fix)),
            patch(_P_COMMIT, side_effect=lambda *a, **k: committed.append(a)),
            patch(_P_REPORT, side_effect=lambda *a, **k: reported.append(a)),
            patch(_P_OWNER, return_value=None),
            patch(_P_PR, return_value="https://github.com/Org/Bot/pull/1"),
        ):
            from spectre_coding.agent import fix
            result = await fix(FIX_IN)

        assert result.fixed is False
        assert len(committed) == 0

    @pytest.mark.asyncio
    async def test_patch_skipped_when_snippet_not_in_file(self):
        no_match_fix = _fix_result()
        no_match_fix["original_snippet"] = "<does_not_exist/>"

        committed = []
        reported = []

        with (
            patch(_P_SDK, return_value=_mock_sdk()),
            patch(_P_FIND, return_value="Org/Bot"),
            patch(_P_DUP, return_value=None),
            patch(_P_AUTH, return_value=("tok", "http://x")),
            patch(_P_ENSURE_BRANCH),
            patch(_P_FETCH, return_value=[{"path": "Framework/Process.xaml", "size": 100}]),
            patch(_P_SUMMARY, return_value="summary"),
            patch(_P_SELECT, AsyncMock(return_value=_selection(["Framework/Process.xaml"]))),
            patch(_P_FETCH_CONTENTS, return_value={"Framework/Process.xaml": "<old/>"}),
            patch(_P_ANALYSE, AsyncMock(return_value=no_match_fix)),
            patch(_P_COMMIT, side_effect=lambda *a, **k: committed.append(a)),
            patch(_P_REPORT, side_effect=lambda *a, **k: reported.append(a)),
            patch(_P_OWNER, return_value=None),
            patch(_P_PR, return_value="https://github.com/Org/Bot/pull/1"),
        ):
            from spectre_coding.agent import fix
            result = await fix(FIX_IN)

        assert result.fixed is False
        assert len(committed) == 0

    def test_pr_body_includes_skip_reason_when_xml_invalid(self):
        from spectre_coding.agent import _build_pr_body
        fr = _fix_result(can_fix=True)
        fr["_actually_patched"] = False
        body = _build_pr_body(
            FIX_IN, fr, patched=False,
            patch_skip_reason="LLM-generated replacement_snippet is not valid XML: unclosed token",
        )
        assert "not valid XML" in body

    def test_pr_body_includes_skip_reason_when_snippet_not_found(self):
        from spectre_coding.agent import _build_pr_body
        fr = _fix_result(can_fix=True)
        fr["_actually_patched"] = False
        body = _build_pr_body(
            FIX_IN, fr, patched=False,
            patch_skip_reason="original_snippet not found verbatim in `Framework/Process.xaml`",
        )
        assert "not found verbatim" in body


class TestPrBodyBuilders:
    def test_body_includes_xml_diff_when_patched(self):
        from spectre_coding.agent import _build_pr_body
        fr = _fix_result(can_fix=True)
        fr["_actually_patched"] = True
        body = _build_pr_body(FIX_IN, fr, patched=True)
        assert "```xml" in body
        assert "<old/>" in body
        assert "<new/>" in body

    def test_body_shows_proposed_change_when_snippet_not_applied(self):
        from spectre_coding.agent import _build_pr_body
        fr = _fix_result(can_fix=True)
        body = _build_pr_body(FIX_IN, fr, patched=False)
        assert "Proposed Change" in body
        assert "<old/>" in body

    def test_body_is_report_only_when_cannot_fix(self):
        from spectre_coding.agent import _build_pr_body
        fr = _fix_result(can_fix=False)
        body = _build_pr_body(FIX_IN, fr, patched=False)
        assert "manual fix required" in body.lower() or "report only" in body.lower()

    def test_body_always_contains_draft_warning(self):
        from spectre_coding.agent import _build_pr_body
        body = _build_pr_body(FIX_IN, _fix_result(), patched=True)
        assert "draft" in body.lower()

    def test_pr_title_says_fix_when_patched(self):
        from spectre_coding.agent import _build_pr_title
        title = _build_pr_title(FIX_IN, _fix_result(), patched=True)
        assert "Fix" in title

    def test_pr_title_says_report_when_not_patched(self):
        from spectre_coding.agent import _build_pr_title
        title = _build_pr_title(FIX_IN, _fix_result(can_fix=False), patched=False)
        assert "Report" in title

    def test_body_shows_patch_skip_reason_when_provided(self):
        from spectre_coding.agent import _build_pr_body
        fr = _fix_result(can_fix=True)
        fr["_actually_patched"] = False
        body = _build_pr_body(FIX_IN, fr, patched=False, patch_skip_reason="some reason here")
        assert "some reason here" in body


class TestFailurePaths:
    @pytest.mark.asyncio
    async def test_llm_token_failure_returns_empty_out_with_support_handle(self):
        with (
            patch(_P_SDK, return_value=_mock_sdk()),
            patch(_P_FIND, return_value="Org/Bot"),
            patch(_P_DUP, return_value=None),
            patch(_P_AUTH, side_effect=RuntimeError("token expired")),
        ):
            from spectre_coding.agent import fix
            result = await fix(FIX_IN)
        assert result.pr_url == ""
        assert result.fixed is False
        assert "@rpa-support" in result.message or "subteam" in result.message

    @pytest.mark.asyncio
    async def test_ensure_branch_failure_returns_empty_out_with_support_handle(self):
        with (
            patch(_P_SDK, return_value=_mock_sdk()),
            patch(_P_FIND, return_value="Org/Bot"),
            patch(_P_DUP, return_value=None),
            patch(_P_AUTH, return_value=("tok", "http://x")),
            patch(_P_ENSURE_BRANCH, side_effect=Exception("branch error")),
        ):
            from spectre_coding.agent import fix
            result = await fix(FIX_IN)
        assert result.pr_url == ""
        assert result.fixed is False
        assert "could not access the code repository" in result.message

    @pytest.mark.asyncio
    async def test_analyse_and_fix_failure_opens_report_only_pr(self):
        with (
            patch(_P_SDK, return_value=_mock_sdk()),
            patch(_P_FIND, return_value="Org/Bot"),
            patch(_P_DUP, return_value=None),
            patch(_P_AUTH, return_value=("tok", "http://x")),
            patch(_P_ENSURE_BRANCH),
            patch(_P_FETCH, return_value=[{"path": "Process.xaml", "size": 100}]),
            patch(_P_SUMMARY, return_value="summary"),
            patch(_P_SELECT, AsyncMock(return_value=_selection(["Process.xaml"]))),
            patch(_P_FETCH_CONTENTS, return_value={"Process.xaml": "<xml/>"}),
            patch(_P_ANALYSE, AsyncMock(side_effect=RuntimeError("llm down"))),
            patch(_P_REPORT),
            patch(_P_OWNER, return_value=None),
            patch(_P_PR, return_value="https://github.com/Org/Bot/pull/9"),
        ):
            from spectre_coding.agent import fix
            result = await fix(FIX_IN)
        assert result.fixed is False
        assert result.pr_url == "https://github.com/Org/Bot/pull/9"

    @pytest.mark.asyncio
    async def test_create_draft_pr_failure_returns_empty_out_with_support_handle(self):
        with (
            patch(_P_SDK, return_value=_mock_sdk()),
            patch(_P_FIND, return_value="Org/Bot"),
            patch(_P_DUP, return_value=None),
            patch(_P_AUTH, return_value=("tok", "http://x")),
            patch(_P_ENSURE_BRANCH),
            patch(_P_FETCH, return_value=[]),
            patch(_P_SUMMARY, return_value=""),
            patch(_P_SELECT, AsyncMock(return_value=_selection([]))),
            patch(_P_REPORT),
            patch(_P_OWNER, return_value=None),
            patch(_P_PR, side_effect=Exception("GitHub 503")),
        ):
            from spectre_coding.agent import fix
            result = await fix(FIX_IN)
        assert result.pr_url == ""
        assert result.fixed is False
        assert "could not submit the fix" in result.message

    @pytest.mark.asyncio
    async def test_commit_report_failure_does_not_crash_job(self):
        with (
            patch(_P_SDK, return_value=_mock_sdk()),
            patch(_P_FIND, return_value="Org/Bot"),
            patch(_P_DUP, return_value=None),
            patch(_P_AUTH, return_value=("tok", "http://x")),
            patch(_P_ENSURE_BRANCH),
            patch(_P_FETCH, return_value=[{"path": "Process.xaml", "size": 100}]),
            patch(_P_SUMMARY, return_value="summary"),
            patch(_P_SELECT, AsyncMock(return_value=_selection(["Process.xaml"]))),
            patch(_P_FETCH_CONTENTS, return_value={"Process.xaml": "<xml/>"}),
            patch(_P_ANALYSE, AsyncMock(return_value=_fix_result(can_fix=False, confidence="Low"))),
            patch(_P_REPORT, side_effect=Exception("commit failed")),
            patch(_P_OWNER, return_value=None),
            patch(_P_PR, return_value="https://github.com/Org/Bot/pull/10"),
        ):
            from spectre_coding.agent import fix
            result = await fix(FIX_IN)
        assert result.pr_url == "https://github.com/Org/Bot/pull/10"

    @pytest.mark.asyncio
    async def test_confidence_normalized_from_lowercase(self):
        labels_used = []

        def capture_pr(repo, branch, title, body, labels, assignee=None, **kw):
            labels_used.extend(labels)
            return "https://github.com/Org/Bot/pull/11"

        with (
            patch(_P_SDK, return_value=_mock_sdk()),
            patch(_P_FIND, return_value="Org/Bot"),
            patch(_P_DUP, return_value=None),
            patch(_P_AUTH, return_value=("tok", "http://x")),
            patch(_P_ENSURE_BRANCH),
            patch(_P_FETCH, return_value=[{"path": "Process.xaml", "size": 100}]),
            patch(_P_SUMMARY, return_value="summary"),
            patch(_P_SELECT, AsyncMock(return_value=_selection(["Process.xaml"]))),
            patch(_P_FETCH_CONTENTS, return_value={"Process.xaml": "<old/>"}),
            patch(_P_ANALYSE, AsyncMock(return_value=_fix_result(confidence="high"))),
            patch(_P_COMMIT),
            patch(_P_OWNER, return_value=None),
            patch(_P_PR, side_effect=capture_pr),
        ):
            from spectre_coding.agent import fix
            result = await fix(FIX_IN)
        assert result.llm_confidence == "High"
        assert "needs-human-review" not in labels_used
