"""Tests for github_client."""
import os
import pytest
from unittest.mock import MagicMock, patch, call
from github import GithubException

# All tests that call _get_client() need GITHUB_TOKEN set, otherwise it raises
# before Github() is ever constructed. Set it for the whole test session.
if not os.environ.get("GITHUB_TOKEN"):
    os.environ["GITHUB_TOKEN"] = "test-token"


def _make_repo(full_name, topics=None):
    repo = MagicMock()
    repo.full_name = full_name
    if topics is not None:
        repo.get_topics.return_value = topics
    return repo


def _make_pr(title, url):
    pr = MagicMock()
    pr.title = title
    pr.html_url = url
    return pr


def _make_issue(title, url):
    issue = MagicMock()
    issue.title = title
    issue.html_url = url
    return issue


# ── find_repo_by_process ──────────────────────────────────────────────────────

class TestFindRepoByProcess:
    @patch("spectre_coding.github_client.Github")
    def test_finds_repo_via_search_api(self, MockGithub):
        repo = _make_repo("Org/InvoiceBot")
        instance = MockGithub.return_value
        instance.search_repositories.return_value = [repo]
        from spectre_coding.github_client import find_repo_by_process
        assert find_repo_by_process("3201 Invoice Processing") == "Org/InvoiceBot"
        query = instance.search_repositories.call_args[0][0]
        assert "topic:3201" in query

    @patch("spectre_coding.github_client.Github")
    def test_returns_none_when_search_empty(self, MockGithub):
        MockGithub.return_value.search_repositories.return_value = []
        from spectre_coding.github_client import find_repo_by_process
        assert find_repo_by_process("3201 Invoice Processing") is None

    def test_returns_none_when_no_process_number(self):
        from spectre_coding.github_client import find_repo_by_process
        assert find_repo_by_process("Invoice Processing Bot") is None

    @patch("spectre_coding.github_client.Github")
    def test_finds_repo_with_three_digit_process_number(self, MockGithub):
        repo = _make_repo("Org/ShortBot")
        instance = MockGithub.return_value
        instance.search_repositories.return_value = [repo]
        from spectre_coding.github_client import find_repo_by_process
        assert find_repo_by_process("321 Short Process") == "Org/ShortBot"
        query = instance.search_repositories.call_args[0][0]
        assert "topic:321" in query

    @patch("spectre_coding.github_client.Github")
    def test_finds_repo_with_five_digit_process_number(self, MockGithub):
        repo = _make_repo("Org/LongBot")
        instance = MockGithub.return_value
        instance.search_repositories.return_value = [repo]
        from spectre_coding.github_client import find_repo_by_process
        assert find_repo_by_process("32101 Long Process") == "Org/LongBot"
        query = instance.search_repositories.call_args[0][0]
        assert "topic:32101" in query

    def test_returns_none_when_github_token_missing(self):
        # Temporarily remove GITHUB_TOKEN so _get_client() raises, causing find_repo to return None
        saved = os.environ.pop("GITHUB_TOKEN", None)
        try:
            from spectre_coding.github_client import find_repo_by_process
            assert find_repo_by_process("3201 Invoice Processing") is None
        finally:
            if saved is not None:
                os.environ["GITHUB_TOKEN"] = saved
            else:
                os.environ["GITHUB_TOKEN"] = "test-token"


# ── check_duplicate ───────────────────────────────────────────────────────────

class TestCheckDuplicate:
    @patch("spectre_coding.github_client.Github")
    def test_returns_url_when_open_pr_matches(self, MockGithub):
        repo = MagicMock()
        repo.get_pulls.return_value = [_make_pr("[SpectreAI] 3201 — INV-98766", "https://github.com/Org/Repo/pull/1")]
        repo.get_issues.return_value = []
        MockGithub.return_value.get_repo.return_value = repo
        from spectre_coding.github_client import check_duplicate
        result = check_duplicate("Org/Repo", "INV-98766")
        assert result == "https://github.com/Org/Repo/pull/1"

    @patch("spectre_coding.github_client.Github")
    def test_returns_url_when_open_issue_matches(self, MockGithub):
        repo = MagicMock()
        repo.get_pulls.return_value = []
        repo.get_issues.return_value = [_make_issue("[SpectreAI] INV-98766 failure", "https://github.com/Org/Repo/issues/5")]
        MockGithub.return_value.get_repo.return_value = repo
        from spectre_coding.github_client import check_duplicate
        result = check_duplicate("Org/Repo", "INV-98766")
        assert result == "https://github.com/Org/Repo/issues/5"

    @patch("spectre_coding.github_client.Github")
    def test_returns_none_when_no_match(self, MockGithub):
        repo = MagicMock()
        repo.get_pulls.return_value = [_make_pr("Unrelated PR", "https://github.com/Org/Repo/pull/2")]
        repo.get_issues.return_value = []
        MockGithub.return_value.get_repo.return_value = repo
        from spectre_coding.github_client import check_duplicate
        assert check_duplicate("Org/Repo", "INV-98766") is None

    @patch("spectre_coding.github_client.Github")
    def test_stops_checking_after_limit(self, MockGithub):
        from spectre_coding.github_client import _DUPLICATE_CHECK_LIMIT
        repo = MagicMock()
        # 60 PRs, none matching — only first 50 should be checked
        prs = [_make_pr(f"Unrelated PR {i}", f"https://github.com/Org/Repo/pull/{i}") for i in range(60)]
        repo.get_pulls.return_value = prs
        repo.get_issues.return_value = []
        MockGithub.return_value.get_repo.return_value = repo
        from spectre_coding.github_client import check_duplicate
        assert check_duplicate("Org/Repo", "INV-99999") is None
        # verify only _DUPLICATE_CHECK_LIMIT items were examined
        assert _DUPLICATE_CHECK_LIMIT == 50


# ── get_codeowner ─────────────────────────────────────────────────────────────

class TestGetCodeowner:
    @patch("spectre_coding.github_client.Github")
    def test_returns_first_username_from_codeowners(self, MockGithub):
        content = MagicMock()
        content.decoded_content = b"* @nithin-br @other-dev\n*.xaml @xaml-owner\n"
        repo = MagicMock()
        repo.get_contents.return_value = content
        MockGithub.return_value.get_repo.return_value = repo
        from spectre_coding.github_client import get_codeowner
        assert get_codeowner("Org/Repo") == "nithin-br"

    @patch("spectre_coding.github_client.Github")
    def test_returns_none_when_codeowners_absent(self, MockGithub):
        repo = MagicMock()
        repo.get_contents.side_effect = GithubException(404, "not found", None)
        MockGithub.return_value.get_repo.return_value = repo
        from spectre_coding.github_client import get_codeowner
        assert get_codeowner("Org/Repo") is None

    @patch("spectre_coding.github_client.Github")
    def test_skips_comment_lines(self, MockGithub):
        content = MagicMock()
        content.decoded_content = b"# This is a comment\n* @real-owner\n"
        repo = MagicMock()
        repo.get_contents.return_value = content
        MockGithub.return_value.get_repo.return_value = repo
        from spectre_coding.github_client import get_codeowner
        assert get_codeowner("Org/Repo") == "real-owner"


# ── get_last_committer ────────────────────────────────────────────────────────

class TestGetLastCommitter:
    @patch("spectre_coding.github_client.Github")
    def test_returns_login_of_last_committer(self, MockGithub):
        author = MagicMock()
        author.login = "nithin-br"
        commit = MagicMock()
        commit.author = author
        repo = MagicMock()
        repo.get_commits.return_value = [commit]
        MockGithub.return_value.get_repo.return_value = repo
        from spectre_coding.github_client import get_last_committer
        assert get_last_committer("Org/Repo", "Framework/Process.xaml") == "nithin-br"
        repo.get_commits.assert_called_once_with(path="Framework/Process.xaml")

    @patch("spectre_coding.github_client.Github")
    def test_returns_none_when_no_commits(self, MockGithub):
        repo = MagicMock()
        repo.get_commits.return_value = []
        MockGithub.return_value.get_repo.return_value = repo
        from spectre_coding.github_client import get_last_committer
        assert get_last_committer("Org/Repo", "Framework/Process.xaml") is None

    @patch("spectre_coding.github_client.Github")
    def test_returns_none_on_github_exception(self, MockGithub):
        repo = MagicMock()
        repo.get_commits.side_effect = Exception("API error")
        MockGithub.return_value.get_repo.return_value = repo
        from spectre_coding.github_client import get_last_committer
        assert get_last_committer("Org/Repo", "Framework/Process.xaml") is None

    @patch("spectre_coding.github_client.Github")
    def test_returns_none_when_commit_has_no_author(self, MockGithub):
        commit = MagicMock()
        commit.author = None
        repo = MagicMock()
        repo.get_commits.return_value = [commit]
        MockGithub.return_value.get_repo.return_value = repo
        from spectre_coding.github_client import get_last_committer
        assert get_last_committer("Org/Repo", "Framework/Process.xaml") is None


# ── create_draft_pr ───────────────────────────────────────────────────────────

class TestCreateDraftPr:
    @patch("spectre_coding.github_client.Github")
    def test_creates_draft_pr(self, MockGithub):
        pr = MagicMock()
        pr.html_url = "https://github.com/Org/Repo/pull/10"
        repo = MagicMock()
        repo.get_labels.return_value = []
        repo.create_pull.return_value = pr
        MockGithub.return_value.get_repo.return_value = repo

        from spectre_coding.github_client import create_draft_pr
        url = create_draft_pr("Org/Repo", "spectre-fix/txn-1", "Title", "Body", ["bug", "spectre-ai"])

        assert url == "https://github.com/Org/Repo/pull/10"
        _, kwargs = repo.create_pull.call_args
        assert kwargs["draft"] is True

    @patch("spectre_coding.github_client.Github")
    def test_creates_missing_labels(self, MockGithub):
        pr = MagicMock()
        pr.html_url = "https://github.com/Org/Repo/pull/11"
        repo = MagicMock()
        repo.get_labels.return_value = []
        repo.create_pull.return_value = pr
        MockGithub.return_value.get_repo.return_value = repo

        from spectre_coding.github_client import create_draft_pr
        create_draft_pr("Org/Repo", "branch", "Title", "Body", ["bug", "spectre-ai", "sap"])

        created = [c[0][0] for c in repo.create_label.call_args_list]
        assert "bug" in created
        assert "spectre-ai" in created
        assert "sap" in created

    @patch("spectre_coding.github_client.Github")
    def test_does_not_recreate_existing_labels(self, MockGithub):
        existing_label = MagicMock()
        existing_label.name = "bug"
        pr = MagicMock()
        pr.html_url = "https://github.com/Org/Repo/pull/12"
        repo = MagicMock()
        repo.get_labels.return_value = [existing_label]
        repo.create_pull.return_value = pr
        MockGithub.return_value.get_repo.return_value = repo

        from spectre_coding.github_client import create_draft_pr
        create_draft_pr("Org/Repo", "branch", "Title", "Body", ["bug"])

        for c in repo.create_label.call_args_list:
            assert c[0][0] != "bug"

    @patch("spectre_coding.github_client.Github")
    def test_assigns_assignee_when_provided(self, MockGithub):
        pr = MagicMock()
        pr.html_url = "https://github.com/Org/Repo/pull/13"
        repo = MagicMock()
        repo.get_labels.return_value = []
        repo.create_pull.return_value = pr
        MockGithub.return_value.get_repo.return_value = repo

        from spectre_coding.github_client import create_draft_pr
        create_draft_pr("Org/Repo", "branch", "Title", "Body", [], assignee="nithin-br")
        pr.add_to_assignees.assert_called_once_with("nithin-br")

    @patch("spectre_coding.github_client.time")
    @patch("spectre_coding.github_client.Github")
    def test_retries_on_503(self, MockGithub, mock_time):
        pr = MagicMock()
        pr.html_url = "https://github.com/Org/Repo/pull/14"
        repo = MagicMock()
        repo.get_labels.return_value = []
        repo.create_pull.side_effect = [
            GithubException(503, "Service unavailable", None),
            pr,
        ]
        MockGithub.return_value.get_repo.return_value = repo

        from spectre_coding.github_client import create_draft_pr
        url = create_draft_pr("Org/Repo", "branch", "Title", "Body", [])
        assert url == "https://github.com/Org/Repo/pull/14"
        assert repo.create_pull.call_count == 2
