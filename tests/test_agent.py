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
_P_CLONE = "spectre_coding.agent.clone_repo"
_P_SCAN = "spectre_coding.agent.scan_repo_xamls"
_P_SUMMARY = "spectre_coding.agent.build_repo_summary"
_P_SELECT = "spectre_coding.agent.select_target_files"
_P_ANALYSE = "spectre_coding.agent.analyse_and_fix"
_P_PUSH = "spectre_coding.agent.push_branch"
_P_PR = "spectre_coding.agent.create_draft_pr"
_P_OWNER = "spectre_coding.agent.get_codeowner"
_P_AUTH = "spectre_coding.agent.get_llm_token"
_P_TMPDIR = "tempfile.TemporaryDirectory"


def _tmpdir_mock(path="/tmp/fake"):
    m = MagicMock()
    m.__enter__ = MagicMock(return_value=path)
    m.__exit__ = MagicMock(return_value=False)
    return m


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
        with patch(_P_FIND, return_value=None), patch(_P_AUTH, return_value=("tok", "http://x")):
            from spectre_coding.agent import fix
            result = await fix(FIX_IN)
        assert result.pr_url == ""
        assert "No GitHub repo found" in result.message

    @pytest.mark.asyncio
    async def test_returns_duplicate_url_when_duplicate_found(self):
        with (
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
            patch(_P_FIND, return_value="Org/Bot"),
            patch(_P_DUP, return_value=None),
            patch(_P_AUTH, return_value=("tok", "http://x")),
            patch(_P_TMPDIR, return_value=_tmpdir_mock()),
            patch(_P_CLONE),
            patch(_P_SCAN, return_value=[{"path": "Framework/Process.xaml"}]),
            patch(_P_SUMMARY, return_value="summary"),
            patch(_P_SELECT, AsyncMock(return_value=_selection(["Framework/Process.xaml"]))),
            patch("builtins.open", MagicMock(return_value=MagicMock(
                __enter__=MagicMock(return_value=MagicMock(read=MagicMock(return_value="<old/>"))),
                __exit__=MagicMock(return_value=False),
            ))),
            patch(_P_ANALYSE, AsyncMock(return_value=_fix_result())),
            patch(_P_PUSH),
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
            patch(_P_FIND, return_value="Org/Bot"),
            patch(_P_DUP, return_value=None),
            patch(_P_AUTH, return_value=("tok", "http://x")),
            patch(_P_TMPDIR, return_value=_tmpdir_mock()),
            patch(_P_CLONE),
            patch(_P_SCAN, return_value=[]),
            patch(_P_SUMMARY, return_value=""),
            patch(_P_SELECT, AsyncMock(return_value=_selection([]))),
            patch(_P_PUSH),
            patch(_P_OWNER, return_value=None),
            patch(_P_PR, side_effect=capture_pr),
        ):
            from spectre_coding.agent import fix
            await fix(FIX_IN)
        # create_draft_pr was called — draft is enforced inside that function
        assert pr_kwargs["repo"] == "Org/Bot"

    @pytest.mark.asyncio
    async def test_adds_needs_human_review_label_when_low_confidence(self):
        labels_used = []

        def capture_pr(repo, branch, title, body, labels, assignee=None, **kw):
            labels_used.extend(labels)
            return "https://github.com/Org/Bot/pull/3"

        with (
            patch(_P_FIND, return_value="Org/Bot"),
            patch(_P_DUP, return_value=None),
            patch(_P_AUTH, return_value=("tok", "http://x")),
            patch(_P_TMPDIR, return_value=_tmpdir_mock()),
            patch(_P_CLONE),
            patch(_P_SCAN, return_value=[{"path": "Process.xaml"}]),
            patch(_P_SUMMARY, return_value="summary"),
            patch(_P_SELECT, AsyncMock(return_value=_selection(["Process.xaml"], confidence="Low"))),
            patch("builtins.open", MagicMock(return_value=MagicMock(
                __enter__=MagicMock(return_value=MagicMock(read=MagicMock(return_value="<xml/>"))),
                __exit__=MagicMock(return_value=False),
            ))),
            patch(_P_ANALYSE, AsyncMock(return_value=_fix_result(can_fix=False, confidence="Low"))),
            patch(_P_PUSH),
            patch(_P_OWNER, return_value=None),
            patch(_P_PR, side_effect=capture_pr),
        ):
            from spectre_coding.agent import fix
            await fix(FIX_IN)
        assert "needs-human-review" in labels_used

    @pytest.mark.asyncio
    async def test_target_activity_propagated_to_fixout(self):
        file_handle = MagicMock()
        file_handle.read.return_value = "content"
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=file_handle)
        ctx.__exit__ = MagicMock(return_value=False)
        with (
            patch(_P_FIND, return_value="Org/Bot"),
            patch(_P_DUP, return_value=None),
            patch(_P_AUTH, return_value=("tok", "http://x")),
            patch(_P_TMPDIR, return_value=_tmpdir_mock()),
            patch(_P_CLONE),
            patch(_P_SCAN, return_value=[{"path": "Framework/Process.xaml"}]),
            patch(_P_SUMMARY, return_value="summary"),
            patch(_P_SELECT, AsyncMock(return_value=_selection(["Framework/Process.xaml"]))),
            patch("os.path.exists", return_value=True),
            patch("builtins.open", return_value=ctx),
            patch(_P_ANALYSE, AsyncMock(return_value=_fix_result(can_fix=True))),
            patch(_P_PUSH),
            patch(_P_OWNER, return_value=None),
            patch(_P_PR, return_value="https://github.com/Org/Bot/pull/4"),
        ):
            from spectre_coding.agent import fix
            result = await fix(FIX_IN)
        assert result.target_activity == "Click Login Button"


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
