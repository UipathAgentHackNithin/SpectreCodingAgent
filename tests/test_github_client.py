"""Tests for github_client — repo discovery and PR creation logic."""
import pytest
from unittest.mock import MagicMock, patch, call


# ── find_repo_by_process ──────────────────────────────────────────────────────

class TestFindRepoByProcess:
    def _make_repo(self, full_name: str, topics: list[str]):
        repo = MagicMock()
        repo.full_name = full_name
        repo.get_topics.return_value = topics
        return repo

    @patch("spectre_coding.github_client.Github")
    def test_finds_repo_matching_process_number(self, MockGithub):
        org = MagicMock()
        org.get_repos.return_value = [
            self._make_repo("Org/InvoiceBot", ["3201-invoice-processing"]),
            self._make_repo("Org/GLBot", ["3202-gl-reconciliation"]),
        ]
        MockGithub.return_value.get_organization.return_value = org

        from spectre_coding.github_client import find_repo_by_process
        result = find_repo_by_process("3201 Invoice Processing")
        assert result == "Org/InvoiceBot"

    @patch("spectre_coding.github_client.Github")
    def test_finds_repo_when_process_name_has_prefix(self, MockGithub):
        org = MagicMock()
        org.get_repos.return_value = [
            self._make_repo("Org/GLBot", ["3202-gl-reconciliation"]),
        ]
        MockGithub.return_value.get_organization.return_value = org

        from spectre_coding.github_client import find_repo_by_process
        result = find_repo_by_process("ICSAUTO-3202 GL Reconciliation")
        assert result == "Org/GLBot"

    @patch("spectre_coding.github_client.Github")
    def test_returns_none_when_no_matching_topic(self, MockGithub):
        org = MagicMock()
        org.get_repos.return_value = [
            self._make_repo("Org/OtherBot", ["9999-other-process"]),
        ]
        MockGithub.return_value.get_organization.return_value = org

        from spectre_coding.github_client import find_repo_by_process
        result = find_repo_by_process("3201 Invoice Processing")
        assert result is None

    def test_returns_none_when_no_process_number_in_name(self):
        from spectre_coding.github_client import find_repo_by_process
        result = find_repo_by_process("Invoice Processing Bot")
        assert result is None

    @patch("spectre_coding.github_client.Github")
    def test_returns_none_when_org_has_no_repos(self, MockGithub):
        org = MagicMock()
        org.get_repos.return_value = []
        MockGithub.return_value.get_organization.return_value = org

        from spectre_coding.github_client import find_repo_by_process
        result = find_repo_by_process("3201 Invoice Processing")
        assert result is None


# ── create_pull_request ───────────────────────────────────────────────────────

class TestCreatePullRequest:
    @patch("spectre_coding.github_client.Github")
    def test_creates_pr_and_returns_url(self, MockGithub):
        pr = MagicMock()
        pr.html_url = "https://github.com/Org/Repo/pull/42"
        repo = MagicMock()
        repo.create_pull.return_value = pr
        MockGithub.return_value.get_repo.return_value = repo

        from spectre_coding.github_client import create_pull_request
        url = create_pull_request("Org/Repo", "spectre-fix/txn-1", "Fix title", "PR body")
        assert url == "https://github.com/Org/Repo/pull/42"
        repo.create_pull.assert_called_once_with(
            title="Fix title",
            body="PR body",
            head="spectre-fix/txn-1",
            base="main",
        )

    @patch("spectre_coding.github_client.Github")
    def test_uses_custom_base_branch(self, MockGithub):
        pr = MagicMock()
        pr.html_url = "https://github.com/Org/Repo/pull/99"
        repo = MagicMock()
        repo.create_pull.return_value = pr
        MockGithub.return_value.get_repo.return_value = repo

        from spectre_coding.github_client import create_pull_request
        create_pull_request("Org/Repo", "branch", "title", "body", base_branch="develop")
        _, kwargs = repo.create_pull.call_args
        assert kwargs["base"] == "develop"
