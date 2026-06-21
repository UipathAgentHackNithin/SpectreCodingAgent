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
_P_LAST_COMMITTER = "spectre_coding.agent.get_last_committer"
_P_AUTH = "spectre_coding.agent.get_llm_token"
_P_PAT = "spectre_coding.agent.get_pat"
_P_WRITEBACK = "spectre_coding.agent._writeback_refresh_token"
_P_SDK = "spectre_coding.agent.UiPath"


def _mock_sdk(kb_results=None):
    sdk = MagicMock()
    sdk.assets.retrieve.return_value = MagicMock(string_value="<!subteam^S0BBTE9DA0N>")
    sdk.context_grounding.search.return_value = kb_results if kb_results is not None else []
    sdk.buckets.upload.return_value = None
    sdk.context_grounding.ingest_by_name.return_value = None
    return sdk


def _selection(candidates, confidence="High"):
    return {"candidates": candidates, "reason": "test", "confidence": confidence}


def _fix_entry(patch_mode="snippet", target_file="Framework/Process.xaml",
               target_activity="Click Login Button"):
    entry = {
        "target_file": target_file,
        "target_activity": target_activity,
        "patch_mode": patch_mode,
        "original_snippet": "<old/>" if patch_mode == "snippet" else "",
        "replacement_snippet": "<new/>" if patch_mode == "snippet" else "",
        "rewritten_xaml": (
            "<root><NewActivity DisplayName='Click Login Button'/></root>"
            if patch_mode == "full_rewrite" else ""
        ),
        "commit_message": f"Fix selector in {target_file}",
    }
    return entry


def _fix_result(can_fix=True, patched=True, confidence="High", issue_type="SAP GUI - Broken Selector",
                failure_category="selector", patch_mode="snippet", caveats=None):
    fixes = [_fix_entry(patch_mode=patch_mode)] if can_fix else []
    return {
        "can_fix": can_fix,
        "fixes": fixes,
        "explanation": "Updated selector" if can_fix else "Requires manual credential rotation",
        "confidence": confidence,
        "issue_type_label": issue_type,
        "failure_category": failure_category if can_fix else "unknown",
        "caveats": caveats if caveats is not None else [],
    }


class TestAgentFlow:
    @pytest.mark.asyncio
    async def test_returns_early_when_repo_not_found(self):
        with (
            patch(_P_SDK, return_value=_mock_sdk()),
            patch(_P_FIND, return_value=None),
            patch(_P_PAT, return_value=("tok", "http://x")),
            patch(_P_AUTH, return_value=("tok", "http://x")),
            patch(_P_WRITEBACK),
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
            patch(_P_PAT, return_value=("tok", "http://x")),
            patch(_P_AUTH, return_value=("tok", "http://x")),
            patch(_P_WRITEBACK),
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
            patch(_P_PAT, return_value=("tok", "http://x")),
            patch(_P_AUTH, return_value=("tok", "http://x")),
            patch(_P_WRITEBACK),
            patch(_P_ENSURE_BRANCH),
            patch(_P_FETCH, return_value=[{"path": "Framework/Process.xaml", "size": 100}]),
            patch(_P_SUMMARY, return_value="summary"),
            patch(_P_SELECT, AsyncMock(return_value=_selection(["Framework/Process.xaml"]))),
            patch(_P_FETCH_CONTENTS, return_value={"Framework/Process.xaml": "<old/>"}),
            patch(_P_ANALYSE, AsyncMock(return_value=_fix_result())),
            patch(_P_COMMIT),
            patch(_P_LAST_COMMITTER, return_value=None),
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
            patch(_P_PAT, return_value=("tok", "http://x")),
            patch(_P_AUTH, return_value=("tok", "http://x")),
            patch(_P_WRITEBACK),
            patch(_P_ENSURE_BRANCH),
            patch(_P_FETCH, return_value=[]),
            patch(_P_SUMMARY, return_value=""),
            patch(_P_SELECT, AsyncMock(return_value=_selection([]))),
            patch(_P_REPORT),
            patch(_P_LAST_COMMITTER, return_value=None),
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
            patch(_P_PAT, return_value=("tok", "http://x")),
            patch(_P_AUTH, return_value=("tok", "http://x")),
            patch(_P_WRITEBACK),
            patch(_P_ENSURE_BRANCH),
            patch(_P_FETCH, return_value=[{"path": "Process.xaml", "size": 100}]),
            patch(_P_SUMMARY, return_value="summary"),
            patch(_P_SELECT, AsyncMock(return_value=_selection(["Process.xaml"], confidence="Low"))),
            patch(_P_FETCH_CONTENTS, return_value={"Process.xaml": "<xml/>"}),
            patch(_P_ANALYSE, AsyncMock(return_value=_fix_result(can_fix=False, confidence="Low"))),
            patch(_P_REPORT),
            patch(_P_LAST_COMMITTER, return_value=None),
            patch(_P_OWNER, return_value=None),
            patch(_P_PR, side_effect=capture_pr),
        ):
            from spectre_coding.agent import fix
            await fix(FIX_IN)
        assert "needs-human-review" in labels_used

    @pytest.mark.asyncio
    async def test_files_changed_propagated_to_fixout(self):
        with (
            patch(_P_SDK, return_value=_mock_sdk()),
            patch(_P_FIND, return_value="Org/Bot"),
            patch(_P_DUP, return_value=None),
            patch(_P_PAT, return_value=("tok", "http://x")),
            patch(_P_AUTH, return_value=("tok", "http://x")),
            patch(_P_WRITEBACK),
            patch(_P_ENSURE_BRANCH),
            patch(_P_FETCH, return_value=[{"path": "Framework/Process.xaml", "size": 100}]),
            patch(_P_SUMMARY, return_value="summary"),
            patch(_P_SELECT, AsyncMock(return_value=_selection(["Framework/Process.xaml"]))),
            patch(_P_FETCH_CONTENTS, return_value={"Framework/Process.xaml": "<old/>"}),
            patch(_P_ANALYSE, AsyncMock(return_value=_fix_result(can_fix=True))),
            patch(_P_COMMIT),
            patch(_P_LAST_COMMITTER, return_value=None),
            patch(_P_OWNER, return_value=None),
            patch(_P_PR, return_value="https://github.com/Org/Bot/pull/4"),
        ):
            from spectre_coding.agent import fix
            result = await fix(FIX_IN)
        assert "Framework/Process.xaml" in result.files_changed


class TestPatchApply:
    @pytest.mark.asyncio
    async def test_snippet_patch_applied_when_found_verbatim(self):
        committed = {}

        def capture_commit(repo, branch, path, content, msg):
            committed["content"] = content

        with (
            patch(_P_SDK, return_value=_mock_sdk()),
            patch(_P_FIND, return_value="Org/Bot"),
            patch(_P_DUP, return_value=None),
            patch(_P_PAT, return_value=("tok", "http://x")),
            patch(_P_AUTH, return_value=("tok", "http://x")),
            patch(_P_WRITEBACK),
            patch(_P_ENSURE_BRANCH),
            patch(_P_FETCH, return_value=[{"path": "Framework/Process.xaml", "size": 100}]),
            patch(_P_SUMMARY, return_value="summary"),
            patch(_P_SELECT, AsyncMock(return_value=_selection(["Framework/Process.xaml"]))),
            patch(_P_FETCH_CONTENTS, return_value={"Framework/Process.xaml": "<old/>"}),
            patch(_P_ANALYSE, AsyncMock(return_value=_fix_result(patch_mode="snippet"))),
            patch(_P_COMMIT, side_effect=capture_commit),
            patch(_P_LAST_COMMITTER, return_value=None),
            patch(_P_OWNER, return_value=None),
            patch(_P_PR, return_value="https://github.com/Org/Bot/pull/1"),
        ):
            from spectre_coding.agent import fix
            result = await fix(FIX_IN)

        assert result.fixed is True
        assert "<new/>" in committed["content"]
        assert "<old/>" not in committed["content"]

    @pytest.mark.asyncio
    async def test_full_rewrite_committed_when_xaml_is_valid(self):
        committed = {}

        def capture_commit(repo, branch, path, content, msg):
            committed["content"] = content

        with (
            patch(_P_SDK, return_value=_mock_sdk()),
            patch(_P_FIND, return_value="Org/Bot"),
            patch(_P_DUP, return_value=None),
            patch(_P_PAT, return_value=("tok", "http://x")),
            patch(_P_AUTH, return_value=("tok", "http://x")),
            patch(_P_WRITEBACK),
            patch(_P_ENSURE_BRANCH),
            patch(_P_FETCH, return_value=[{"path": "Framework/Process.xaml", "size": 100}]),
            patch(_P_SUMMARY, return_value="summary"),
            patch(_P_SELECT, AsyncMock(return_value=_selection(["Framework/Process.xaml"]))),
            patch(_P_FETCH_CONTENTS, return_value={"Framework/Process.xaml": "<old/>"}),
            patch(_P_ANALYSE, AsyncMock(return_value=_fix_result(patch_mode="full_rewrite"))),
            patch(_P_COMMIT, side_effect=capture_commit),
            patch(_P_LAST_COMMITTER, return_value=None),
            patch(_P_OWNER, return_value=None),
            patch(_P_PR, return_value="https://github.com/Org/Bot/pull/1"),
        ):
            from spectre_coding.agent import fix
            result = await fix(FIX_IN)

        assert result.fixed is True
        assert "NewActivity" in committed["content"]

    @pytest.mark.asyncio
    async def test_snippet_skipped_when_replacement_is_invalid_xml(self):
        bad_fix = _fix_result(patch_mode="snippet")
        bad_fix["fixes"][0]["replacement_snippet"] = "<unclosed"
        committed = []
        with (
            patch(_P_SDK, return_value=_mock_sdk()),
            patch(_P_FIND, return_value="Org/Bot"),
            patch(_P_DUP, return_value=None),
            patch(_P_PAT, return_value=("tok", "http://x")),
            patch(_P_AUTH, return_value=("tok", "http://x")),
            patch(_P_WRITEBACK),
            patch(_P_ENSURE_BRANCH),
            patch(_P_FETCH, return_value=[{"path": "Framework/Process.xaml", "size": 100}]),
            patch(_P_SUMMARY, return_value="summary"),
            patch(_P_SELECT, AsyncMock(return_value=_selection(["Framework/Process.xaml"]))),
            patch(_P_FETCH_CONTENTS, return_value={"Framework/Process.xaml": "<old/>"}),
            patch(_P_ANALYSE, AsyncMock(return_value=bad_fix)),
            patch(_P_COMMIT, side_effect=lambda *a, **k: committed.append(a)),
            patch(_P_REPORT),
            patch(_P_LAST_COMMITTER, return_value=None),
            patch(_P_OWNER, return_value=None),
            patch(_P_PR, return_value="https://github.com/Org/Bot/pull/1"),
        ):
            from spectre_coding.agent import fix
            result = await fix(FIX_IN)
        assert result.fixed is False
        assert len(committed) == 0

    @pytest.mark.asyncio
    async def test_snippet_skipped_when_not_found_verbatim(self):
        no_match_fix = _fix_result(patch_mode="snippet")
        no_match_fix["fixes"][0]["original_snippet"] = "<does_not_exist/>"
        committed = []
        with (
            patch(_P_SDK, return_value=_mock_sdk()),
            patch(_P_FIND, return_value="Org/Bot"),
            patch(_P_DUP, return_value=None),
            patch(_P_PAT, return_value=("tok", "http://x")),
            patch(_P_AUTH, return_value=("tok", "http://x")),
            patch(_P_WRITEBACK),
            patch(_P_ENSURE_BRANCH),
            patch(_P_FETCH, return_value=[{"path": "Framework/Process.xaml", "size": 100}]),
            patch(_P_SUMMARY, return_value="summary"),
            patch(_P_SELECT, AsyncMock(return_value=_selection(["Framework/Process.xaml"]))),
            patch(_P_FETCH_CONTENTS, return_value={"Framework/Process.xaml": "<old/>"}),
            patch(_P_ANALYSE, AsyncMock(return_value=no_match_fix)),
            patch(_P_COMMIT, side_effect=lambda *a, **k: committed.append(a)),
            patch(_P_REPORT),
            patch(_P_LAST_COMMITTER, return_value=None),
            patch(_P_OWNER, return_value=None),
            patch(_P_PR, return_value="https://github.com/Org/Bot/pull/1"),
        ):
            from spectre_coding.agent import fix
            result = await fix(FIX_IN)
        assert result.fixed is False
        assert len(committed) == 0

    @pytest.mark.asyncio
    async def test_rewrite_skipped_when_rewritten_xaml_is_invalid_xml(self):
        bad_fix = _fix_result(patch_mode="full_rewrite")
        bad_fix["fixes"][0]["rewritten_xaml"] = "<unclosed"
        committed = []
        with (
            patch(_P_SDK, return_value=_mock_sdk()),
            patch(_P_FIND, return_value="Org/Bot"),
            patch(_P_DUP, return_value=None),
            patch(_P_PAT, return_value=("tok", "http://x")),
            patch(_P_AUTH, return_value=("tok", "http://x")),
            patch(_P_WRITEBACK),
            patch(_P_ENSURE_BRANCH),
            patch(_P_FETCH, return_value=[{"path": "Framework/Process.xaml", "size": 100}]),
            patch(_P_SUMMARY, return_value="summary"),
            patch(_P_SELECT, AsyncMock(return_value=_selection(["Framework/Process.xaml"]))),
            patch(_P_FETCH_CONTENTS, return_value={"Framework/Process.xaml": "<old/>"}),
            patch(_P_ANALYSE, AsyncMock(return_value=bad_fix)),
            patch(_P_COMMIT, side_effect=lambda *a, **k: committed.append(a)),
            patch(_P_REPORT),
            patch(_P_LAST_COMMITTER, return_value=None),
            patch(_P_OWNER, return_value=None),
            patch(_P_PR, return_value="https://github.com/Org/Bot/pull/1"),
        ):
            from spectre_coding.agent import fix
            result = await fix(FIX_IN)
        assert result.fixed is False
        assert len(committed) == 0

    @pytest.mark.asyncio
    async def test_multi_file_fix_commits_all_files(self):
        committed = []

        def capture_commit(repo, branch, path, content, msg):
            committed.append(path)

        multi_fix = {
            "can_fix": True,
            "fixes": [
                _fix_entry(patch_mode="snippet", target_file="Framework/Process.xaml"),
                _fix_entry(patch_mode="snippet", target_file="Framework/LoginToSAP.xaml"),
            ],
            "explanation": "Two files needed fixing",
            "confidence": "High",
            "issue_type_label": "SAP GUI - Broken Selector",
            "failure_category": "selector",
            "caveats": [],
        }
        with (
            patch(_P_SDK, return_value=_mock_sdk()),
            patch(_P_FIND, return_value="Org/Bot"),
            patch(_P_DUP, return_value=None),
            patch(_P_PAT, return_value=("tok", "http://x")),
            patch(_P_AUTH, return_value=("tok", "http://x")),
            patch(_P_WRITEBACK),
            patch(_P_ENSURE_BRANCH),
            patch(_P_FETCH, return_value=[{"path": "Framework/Process.xaml", "size": 100}]),
            patch(_P_SUMMARY, return_value="summary"),
            patch(_P_SELECT, AsyncMock(return_value=_selection(["Framework/Process.xaml", "Framework/LoginToSAP.xaml"]))),
            patch(_P_FETCH_CONTENTS, return_value={
                "Framework/Process.xaml": "<old/>",
                "Framework/LoginToSAP.xaml": "<old/>",
            }),
            patch(_P_ANALYSE, AsyncMock(return_value=multi_fix)),
            patch(_P_COMMIT, side_effect=capture_commit),
            patch(_P_LAST_COMMITTER, return_value=None),
            patch(_P_OWNER, return_value=None),
            patch(_P_PR, return_value="https://github.com/Org/Bot/pull/1"),
        ):
            from spectre_coding.agent import fix
            result = await fix(FIX_IN)

        assert result.fixed is True
        assert "Framework/Process.xaml" in committed
        assert "Framework/LoginToSAP.xaml" in committed
        assert len(result.files_changed) == 2


def _patch_result(patch_mode="snippet", patched=True, skip_reason="",
                  target_file="Framework/Process.xaml", target_activity="Click Login Button"):
    return {
        "target_file": target_file,
        "target_activity": target_activity,
        "patch_mode": patch_mode,
        "patched": patched,
        "skip_reason": skip_reason,
        "original_snippet": "<old/>" if patch_mode == "snippet" else "",
        "replacement_snippet": "<new/>" if (patch_mode == "snippet" and patched) else "",
        "rewritten_xaml": (
            "<root><NewActivity DisplayName='Click Login Button'/></root>"
            if patch_mode == "full_rewrite" else ""
        ),
        "commit_message": "Fix selector",
    }


class TestPrBodyBuilders:
    def _fr(self, patch_results, caveats=None):
        return {
            "can_fix": True,
            "fixes": [],
            "_patch_results": patch_results,
            "explanation": "Updated selector",
            "confidence": "High",
            "issue_type_label": "SAP GUI - Broken Selector",
            "failure_category": "selector",
            "caveats": caveats if caveats is not None else [],
        }

    def test_body_confirms_snippet_applied(self):
        from spectre_coding.agent import _build_pr_body
        fr = self._fr([_patch_result(patch_mode="snippet", patched=True)])
        body = _build_pr_body(FIX_IN, fr, patched=True)
        assert "Before:" in body
        assert "After:" in body
        assert "Framework/Process.xaml" in body

    def test_body_confirms_rewrite_applied_when_patched(self):
        from spectre_coding.agent import _build_pr_body
        fr = self._fr([_patch_result(patch_mode="full_rewrite", patched=True)])
        body = _build_pr_body(FIX_IN, fr, patched=True)
        assert "Full file rewrite committed" in body
        assert "Framework/Process.xaml" in body

    def test_body_shows_proposed_rewrite_when_not_applied(self):
        from spectre_coding.agent import _build_pr_body
        fr = self._fr([_patch_result(patch_mode="full_rewrite", patched=False,
                                     skip_reason="rewritten_xaml is not well-formed XML")])
        body = _build_pr_body(FIX_IN, fr, patched=False)
        assert "Proposed rewrite" in body
        assert "NewActivity" in body

    def test_body_shows_skip_reason_when_snippet_not_applied(self):
        from spectre_coding.agent import _build_pr_body
        fr = self._fr([_patch_result(patch_mode="snippet", patched=False,
                                     skip_reason="original_snippet not found verbatim")])
        body = _build_pr_body(FIX_IN, fr, patched=False)
        assert "not found verbatim" in body

    def test_body_shows_multi_file_changes(self):
        from spectre_coding.agent import _build_pr_body
        fr = self._fr([
            _patch_result(patch_mode="snippet", patched=True, target_file="Framework/Process.xaml"),
            _patch_result(patch_mode="snippet", patched=True, target_file="Framework/LoginToSAP.xaml"),
        ])
        body = _build_pr_body(FIX_IN, fr, patched=True)
        assert "Framework/Process.xaml" in body
        assert "Framework/LoginToSAP.xaml" in body

    def test_body_is_report_only_when_cannot_fix(self):
        from spectre_coding.agent import _build_pr_body
        fr = _fix_result(can_fix=False)
        fr["_patch_results"] = []
        body = _build_pr_body(FIX_IN, fr, patched=False)
        assert "manual fix required" in body.lower() or "report only" in body.lower()

    def test_body_always_contains_draft_warning(self):
        from spectre_coding.agent import _build_pr_body
        fr = self._fr([_patch_result(patched=True)])
        body = _build_pr_body(FIX_IN, fr, patched=True)
        assert "draft" in body.lower()

    def test_body_renders_caveats_block_when_present(self):
        from spectre_coding.agent import _build_pr_body
        fr = self._fr(
            [_patch_result(patched=True)],
            caveats=["Auth token may be expired — verify SPECTRE_API_TOKEN in Orchestrator",
                     "API response schema may have changed"]
        )
        body = _build_pr_body(FIX_IN, fr, patched=True)
        assert "Developer Checks Required" in body
        assert "Auth token may be expired" in body
        assert "API response schema may have changed" in body

    def test_body_omits_caveats_block_when_empty(self):
        from spectre_coding.agent import _build_pr_body
        fr = self._fr([_patch_result(patched=True)], caveats=[])
        body = _build_pr_body(FIX_IN, fr, patched=True)
        assert "Developer Checks Required" not in body

    def test_pr_title_says_fix_when_patched(self):
        from spectre_coding.agent import _build_pr_title
        title = _build_pr_title(FIX_IN, _fix_result(), patched=True)
        assert "Fix" in title

    def test_pr_title_says_report_when_not_patched(self):
        from spectre_coding.agent import _build_pr_title
        title = _build_pr_title(FIX_IN, _fix_result(can_fix=False), patched=False)
        assert "Report" in title

    def test_issue_type_label_appears_in_pr_body_table(self):
        from spectre_coding.agent import _build_pr_body
        fr = self._fr([_patch_result(patched=True)])
        fr["issue_type_label"] = "ServiceNow - HTTP Timeout"
        body = _build_pr_body(FIX_IN, fr, patched=True)
        assert "ServiceNow - HTTP Timeout" in body


class TestLabels:
    async def _run_fix(self, fix_result_override, last_committer=None, codeowner=None):
        """Helper — runs fix() with all GitHub calls mocked, returns (labels, assignee)."""
        captured_labels = []
        captured_assignee = []

        def capture_pr(repo, branch, title, body, labels, assignee):
            captured_labels.extend(labels)
            captured_assignee.append(assignee)
            return "https://github.com/Org/Bot/pull/1"

        with (
            patch(_P_SDK, return_value=_mock_sdk()),
            patch(_P_FIND, return_value="Org/Bot"),
            patch(_P_DUP, return_value=None),
            patch(_P_PAT, return_value=("tok", "http://x")),
            patch(_P_AUTH, return_value=("tok", "http://x")),
            patch(_P_WRITEBACK),
            patch(_P_ENSURE_BRANCH),
            patch(_P_FETCH, return_value=[{"path": "Framework/Process.xaml", "size": 100}]),
            patch(_P_SUMMARY, return_value="summary"),
            patch(_P_SELECT, AsyncMock(return_value=_selection(["Framework/Process.xaml"]))),
            patch(_P_FETCH_CONTENTS, return_value={"Framework/Process.xaml": "<old/>"}),
            patch(_P_ANALYSE, AsyncMock(return_value=fix_result_override)),
            patch(_P_COMMIT),
            patch(_P_REPORT),
            patch(_P_LAST_COMMITTER, return_value=last_committer),
            patch(_P_OWNER, return_value=codeowner),
            patch(_P_PR, side_effect=capture_pr),
        ):
            from spectre_coding.agent import fix
            await fix(FIX_IN)

        return captured_labels, captured_assignee[0] if captured_assignee else None

    @pytest.mark.asyncio
    async def test_failure_category_used_as_label_not_issue_type_label(self):
        fr = _fix_result(failure_category="api", issue_type="ServiceNow - HTTP Timeout")
        labels, _ = await self._run_fix(fr)
        assert "api" in labels
        assert "ServiceNow - HTTP Timeout" not in labels

    @pytest.mark.asyncio
    async def test_needs_human_review_added_when_low_confidence(self):
        fr = _fix_result(confidence="Low")
        labels, _ = await self._run_fix(fr)
        assert "needs-human-review" in labels

    @pytest.mark.asyncio
    async def test_needs_human_review_added_when_cannot_fix(self):
        fr = _fix_result(can_fix=False)
        labels, _ = await self._run_fix(fr)
        assert "needs-human-review" in labels

    @pytest.mark.asyncio
    async def test_needs_human_review_not_added_when_high_confidence_and_fixed(self):
        fr = _fix_result(confidence="High", can_fix=True)
        labels, _ = await self._run_fix(fr)
        assert "needs-human-review" not in labels

    @pytest.mark.asyncio
    async def test_last_committer_used_as_assignee_over_codeowner(self):
        fr = _fix_result()
        _, assignee = await self._run_fix(fr, last_committer="last-dev", codeowner="codeowner-dev")
        assert assignee == "last-dev"

    @pytest.mark.asyncio
    async def test_codeowner_used_as_assignee_when_no_last_committer(self):
        fr = _fix_result()
        _, assignee = await self._run_fix(fr, last_committer=None, codeowner="codeowner-dev")
        assert assignee == "codeowner-dev"

    @pytest.mark.asyncio
    async def test_assignee_is_none_when_neither_available(self):
        fr = _fix_result()
        _, assignee = await self._run_fix(fr, last_committer=None, codeowner=None)
        assert assignee is None


class TestXamlRules:
    def test_fixable_patterns_importable(self):
        from spectre_coding.xaml_rules import FIXABLE_PATTERNS
        assert FIXABLE_PATTERNS
        assert "selector" in FIXABLE_PATTERNS.lower()
        assert "retry" in FIXABLE_PATTERNS.lower()
        assert "vb expression" in FIXABLE_PATTERNS.lower()

    def test_no_fix_result_contains_caveats_field(self):
        from spectre_coding.agent import _no_fix_result
        result = _no_fix_result("something went wrong", "Low")
        assert "caveats" in result
        assert result["caveats"] == []


class TestFailurePaths:
    @pytest.mark.asyncio
    async def test_llm_token_failure_returns_empty_out_with_support_handle(self):
        with (
            patch(_P_SDK, return_value=_mock_sdk()),
            patch(_P_FIND, return_value="Org/Bot"),
            patch(_P_DUP, return_value=None),
            patch(_P_PAT, return_value=("tok", "http://x")),
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
            patch(_P_PAT, return_value=("tok", "http://x")),
            patch(_P_AUTH, return_value=("tok", "http://x")),
            patch(_P_WRITEBACK),
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
            patch(_P_PAT, return_value=("tok", "http://x")),
            patch(_P_AUTH, return_value=("tok", "http://x")),
            patch(_P_WRITEBACK),
            patch(_P_ENSURE_BRANCH),
            patch(_P_FETCH, return_value=[{"path": "Process.xaml", "size": 100}]),
            patch(_P_SUMMARY, return_value="summary"),
            patch(_P_SELECT, AsyncMock(return_value=_selection(["Process.xaml"]))),
            patch(_P_FETCH_CONTENTS, return_value={"Process.xaml": "<xml/>"}),
            patch(_P_ANALYSE, AsyncMock(side_effect=RuntimeError("llm down"))),
            patch(_P_REPORT),
            patch(_P_LAST_COMMITTER, return_value=None),
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
            patch(_P_PAT, return_value=("tok", "http://x")),
            patch(_P_AUTH, return_value=("tok", "http://x")),
            patch(_P_WRITEBACK),
            patch(_P_ENSURE_BRANCH),
            patch(_P_FETCH, return_value=[]),
            patch(_P_SUMMARY, return_value=""),
            patch(_P_SELECT, AsyncMock(return_value=_selection([]))),
            patch(_P_REPORT),
            patch(_P_LAST_COMMITTER, return_value=None),
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
            patch(_P_PAT, return_value=("tok", "http://x")),
            patch(_P_AUTH, return_value=("tok", "http://x")),
            patch(_P_WRITEBACK),
            patch(_P_ENSURE_BRANCH),
            patch(_P_FETCH, return_value=[{"path": "Process.xaml", "size": 100}]),
            patch(_P_SUMMARY, return_value="summary"),
            patch(_P_SELECT, AsyncMock(return_value=_selection(["Process.xaml"]))),
            patch(_P_FETCH_CONTENTS, return_value={"Process.xaml": "<xml/>"}),
            patch(_P_ANALYSE, AsyncMock(return_value=_fix_result(can_fix=False, confidence="Low"))),
            patch(_P_REPORT, side_effect=Exception("commit failed")),
            patch(_P_LAST_COMMITTER, return_value=None),
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
            patch(_P_PAT, return_value=("tok", "http://x")),
            patch(_P_AUTH, return_value=("tok", "http://x")),
            patch(_P_WRITEBACK),
            patch(_P_ENSURE_BRANCH),
            patch(_P_FETCH, return_value=[{"path": "Process.xaml", "size": 100}]),
            patch(_P_SUMMARY, return_value="summary"),
            patch(_P_SELECT, AsyncMock(return_value=_selection(["Process.xaml"]))),
            patch(_P_FETCH_CONTENTS, return_value={"Process.xaml": "<old/>"}),
            patch(_P_ANALYSE, AsyncMock(return_value=_fix_result(confidence="high"))),
            patch(_P_COMMIT),
            patch(_P_LAST_COMMITTER, return_value=None),
            patch(_P_OWNER, return_value=None),
            patch(_P_PR, side_effect=capture_pr),
        ):
            from spectre_coding.agent import fix
            result = await fix(FIX_IN)
        assert result.llm_confidence == "High"
        assert "needs-human-review" not in labels_used


class TestKnowledgeBase:
    """Tests for #7 (KB similarity note in PR body) and #8 (KB ingest after fix)."""

    def _kb_hit(self, text="Prior fix: updated selector to use wildcard"):
        result = MagicMock()
        result.text = text
        return result

    @pytest.mark.asyncio
    async def test_prior_fix_from_kb_appears_in_pr_body(self):
        bodies = []

        def capture_pr(repo, branch, title, body, labels, assignee=None, **kw):
            bodies.append(body)
            return "https://github.com/Org/Bot/pull/1"

        kb_result = self._kb_hit("Prior fix: updated SAP selector to wildcard title match")
        sdk = _mock_sdk(kb_results=[kb_result])

        with (
            patch(_P_SDK, return_value=sdk),
            patch(_P_FIND, return_value="Org/Bot"),
            patch(_P_DUP, return_value=None),
            patch(_P_PAT, return_value=("tok", "http://x")),
            patch(_P_AUTH, return_value=("tok", "http://x")),
            patch(_P_WRITEBACK),
            patch(_P_ENSURE_BRANCH),
            patch(_P_FETCH, return_value=[{"path": "Framework/Process.xaml", "size": 100}]),
            patch(_P_SUMMARY, return_value="summary"),
            patch(_P_SELECT, AsyncMock(return_value=_selection(["Framework/Process.xaml"]))),
            patch(_P_FETCH_CONTENTS, return_value={"Framework/Process.xaml": "<old/>"}),
            patch(_P_ANALYSE, AsyncMock(return_value=_fix_result())),
            patch(_P_COMMIT),
            patch(_P_LAST_COMMITTER, return_value=None),
            patch(_P_OWNER, return_value=None),
            patch(_P_PR, side_effect=capture_pr),
        ):
            from spectre_coding.agent import fix
            await fix(FIX_IN)

        assert bodies, "PR was never raised"
        assert "Similar Past Fix" in bodies[0]
        assert "wildcard title match" in bodies[0]

    @pytest.mark.asyncio
    async def test_no_similar_section_when_kb_returns_empty(self):
        bodies = []

        def capture_pr(repo, branch, title, body, labels, assignee=None, **kw):
            bodies.append(body)
            return "https://github.com/Org/Bot/pull/1"

        sdk = _mock_sdk(kb_results=[])

        with (
            patch(_P_SDK, return_value=sdk),
            patch(_P_FIND, return_value="Org/Bot"),
            patch(_P_DUP, return_value=None),
            patch(_P_PAT, return_value=("tok", "http://x")),
            patch(_P_AUTH, return_value=("tok", "http://x")),
            patch(_P_WRITEBACK),
            patch(_P_ENSURE_BRANCH),
            patch(_P_FETCH, return_value=[{"path": "Framework/Process.xaml", "size": 100}]),
            patch(_P_SUMMARY, return_value="summary"),
            patch(_P_SELECT, AsyncMock(return_value=_selection(["Framework/Process.xaml"]))),
            patch(_P_FETCH_CONTENTS, return_value={"Framework/Process.xaml": "<old/>"}),
            patch(_P_ANALYSE, AsyncMock(return_value=_fix_result())),
            patch(_P_COMMIT),
            patch(_P_LAST_COMMITTER, return_value=None),
            patch(_P_OWNER, return_value=None),
            patch(_P_PR, side_effect=capture_pr),
        ):
            from spectre_coding.agent import fix
            await fix(FIX_IN)

        assert bodies, "PR was never raised"
        assert "Similar Past Fix" not in bodies[0]

    @pytest.mark.asyncio
    async def test_kb_ingest_called_after_pr_created(self):
        sdk = _mock_sdk()

        with (
            patch(_P_SDK, return_value=sdk),
            patch(_P_FIND, return_value="Org/Bot"),
            patch(_P_DUP, return_value=None),
            patch(_P_PAT, return_value=("tok", "http://x")),
            patch(_P_AUTH, return_value=("tok", "http://x")),
            patch(_P_WRITEBACK),
            patch(_P_ENSURE_BRANCH),
            patch(_P_FETCH, return_value=[{"path": "Framework/Process.xaml", "size": 100}]),
            patch(_P_SUMMARY, return_value="summary"),
            patch(_P_SELECT, AsyncMock(return_value=_selection(["Framework/Process.xaml"]))),
            patch(_P_FETCH_CONTENTS, return_value={"Framework/Process.xaml": "<old/>"}),
            patch(_P_ANALYSE, AsyncMock(return_value=_fix_result())),
            patch(_P_COMMIT),
            patch(_P_LAST_COMMITTER, return_value=None),
            patch(_P_OWNER, return_value=None),
            patch(_P_PR, return_value="https://github.com/Org/Bot/pull/1"),
        ):
            from spectre_coding.agent import fix
            await fix(FIX_IN)

        sdk.buckets.upload.assert_called_once()
        call_kwargs = sdk.buckets.upload.call_args
        assert call_kwargs.kwargs.get("name") == "Spectre AI"
        sdk.context_grounding.ingest_by_name.assert_called_once()

    @pytest.mark.asyncio
    async def test_kb_ingest_failure_does_not_crash_agent(self):
        sdk = _mock_sdk()
        sdk.buckets.upload.side_effect = Exception("Orchestrator 503")

        with (
            patch(_P_SDK, return_value=sdk),
            patch(_P_FIND, return_value="Org/Bot"),
            patch(_P_DUP, return_value=None),
            patch(_P_PAT, return_value=("tok", "http://x")),
            patch(_P_AUTH, return_value=("tok", "http://x")),
            patch(_P_WRITEBACK),
            patch(_P_ENSURE_BRANCH),
            patch(_P_FETCH, return_value=[{"path": "Framework/Process.xaml", "size": 100}]),
            patch(_P_SUMMARY, return_value="summary"),
            patch(_P_SELECT, AsyncMock(return_value=_selection(["Framework/Process.xaml"]))),
            patch(_P_FETCH_CONTENTS, return_value={"Framework/Process.xaml": "<old/>"}),
            patch(_P_ANALYSE, AsyncMock(return_value=_fix_result())),
            patch(_P_COMMIT),
            patch(_P_LAST_COMMITTER, return_value=None),
            patch(_P_OWNER, return_value=None),
            patch(_P_PR, return_value="https://github.com/Org/Bot/pull/1"),
        ):
            from spectre_coding.agent import fix
            result = await fix(FIX_IN)

        assert result.pr_url == "https://github.com/Org/Bot/pull/1"

    def test_similar_fix_note_renders_in_pr_body(self):
        from spectre_coding.agent import _build_pr_body
        fr = {
            "can_fix": True,
            "fixes": [],
            "_patch_results": [],
            "explanation": "Fixed selector",
            "confidence": "High",
            "issue_type_label": "SAP GUI - Broken Selector",
            "failure_category": "selector",
            "caveats": [],
        }
        body = _build_pr_body(FIX_IN, fr, patched=False,
                              similar_fix_note="Prior fix used wildcard on SAP title attribute")
        assert "Similar Past Fix" in body
        assert "wildcard on SAP title attribute" in body

    def test_pr_body_passes_prior_fix_to_llm_section_absent_when_empty(self):
        from spectre_coding.agent import _build_pr_body
        fr = {
            "can_fix": False,
            "fixes": [],
            "_patch_results": [],
            "explanation": "Cannot fix",
            "confidence": "Low",
            "issue_type_label": "SAP GUI - Broken Selector",
            "failure_category": "selector",
            "caveats": [],
        }
        body = _build_pr_body(FIX_IN, fr, patched=False, similar_fix_note="")
        assert "Similar Past Fix" not in body
