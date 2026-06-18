"""
GitHub client for SpectreCodingAgent.
Handles repo discovery, duplicate detection, cloning, branch push, and draft PR creation.
"""
import os
import re
from typing import Optional
from github import Github, GithubException

try:
    from .logger import get_logger
except ImportError:
    from logger import get_logger

log = get_logger("spectre.github")

_GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
_GITHUB_ORG = os.getenv("GITHUB_ORG", "UipathAgentHackNithin")


def _get_client() -> Github:
    return Github(_GITHUB_TOKEN)


def find_repo_by_process(process_name: str) -> Optional[str]:
    """
    Find GitHub repo by searching org repos for a topic matching the process number.
    Topic format: "3201-invoice-processing"
    """
    match = re.search(r"\b(\d{4})\b", process_name)
    if not match:
        log.warning(f"No 4-digit process number found in: {process_name}")
        return None
    process_number = match.group(1)

    g = _get_client()
    org = g.get_organization(_GITHUB_ORG)
    for repo in org.get_repos():
        for topic in repo.get_topics():
            if topic.startswith(process_number):
                log.info(f"Found repo {repo.full_name} via topic {topic}")
                return repo.full_name
    log.warning(f"No repo found for process number {process_number}")
    return None


def check_duplicate(repo_full_name: str, transaction_id: str) -> Optional[str]:
    """
    Check if an open PR or issue already exists for this transaction_id.
    Returns the URL if found, None otherwise.
    """
    g = _get_client()
    repo = g.get_repo(repo_full_name)

    for pr in repo.get_pulls(state="open"):
        if transaction_id in pr.title:
            log.info(f"Duplicate PR found: {pr.html_url}")
            return pr.html_url

    for issue in repo.get_issues(state="open"):
        if transaction_id in issue.title:
            log.info(f"Duplicate issue found: {issue.html_url}")
            return issue.html_url

    return None


def get_codeowner(repo_full_name: str) -> Optional[str]:
    """
    Read CODEOWNERS from repo root or .github/ and return the first GitHub username.
    Returns None if file absent or no username found.
    """
    g = _get_client()
    repo = g.get_repo(repo_full_name)
    for path in ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS"):
        try:
            content = repo.get_contents(path)
            text = content.decoded_content.decode("utf-8")
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                # Lines like: * @username  or  *.xaml @username @other
                match = re.search(r"@([\w-]+)", line)
                if match:
                    return match.group(1)
        except GithubException:
            continue
    return None


def clone_repo(repo_full_name: str, target_dir: str) -> str:
    """Clone repo into target_dir using token auth."""
    import git
    clone_url = f"https://{_GITHUB_TOKEN}@github.com/{repo_full_name}.git"
    log.info(f"Cloning {repo_full_name}")
    git.Repo.clone_from(clone_url, target_dir)
    return target_dir


def push_branch(local_repo_path: str, branch_name: str, commit_message: str) -> None:
    """Stage all changes, commit, and push branch."""
    import git
    repo = git.Repo(local_repo_path)
    repo.git.checkout("-b", branch_name)
    repo.git.add(A=True)
    repo.index.commit(commit_message)
    repo.remote("origin").push(refspec=f"{branch_name}:{branch_name}")
    log.info(f"Pushed branch {branch_name}")


def _ensure_labels(repo, labels: list[str]) -> None:
    """Create any labels that don't already exist."""
    existing = {l.name for l in repo.get_labels()}
    label_colors = {
        "bug": "d73a4a",
        "spectre-ai": "0075ca",
        "needs-human-review": "e4e669",
        "sap": "f9d0c4",
        "api": "c2e0c6",
        "ui-automation": "bfd4f2",
        "data": "d4c5f9",
        "unknown": "eeeeee",
    }
    for name in labels:
        if name not in existing:
            try:
                repo.create_label(name, label_colors.get(name, "cccccc"))
            except GithubException:
                pass


def create_draft_pr(
    repo_full_name: str,
    branch_name: str,
    title: str,
    body: str,
    labels: list[str],
    assignee: Optional[str] = None,
    base_branch: str = "main",
) -> str:
    """Create a DRAFT pull request with labels and optional assignee."""
    g = _get_client()
    repo = g.get_repo(repo_full_name)

    _ensure_labels(repo, labels)

    pr = repo.create_pull(
        title=title,
        body=body,
        head=branch_name,
        base=base_branch,
        draft=True,
    )

    if labels:
        pr.add_to_labels(*labels)

    if assignee:
        try:
            pr.add_to_assignees(assignee)
        except GithubException:
            log.warning(f"Could not assign {assignee} to PR")

    log.info(f"Draft PR created: {pr.html_url}")
    return pr.html_url
