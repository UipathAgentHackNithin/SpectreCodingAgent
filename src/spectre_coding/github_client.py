import os
import re
import tempfile
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
    Find GitHub repo for a process by searching org repos with topic matching.
    Topic format: <process_number>-<slug>  e.g. "3201-invoice-processing"
    Extracts the process number from the process name.
    """
    match = re.search(r"\b(\d{4})\b", process_name)
    if not match:
        log.warning(f"Could not extract process number from: {process_name}")
        return None
    process_number = match.group(1)

    g = _get_client()
    org = g.get_organization(_GITHUB_ORG)
    for repo in org.get_repos():
        topics = repo.get_topics()
        for topic in topics:
            if topic.startswith(process_number):
                log.info(f"Found repo {repo.full_name} with topic {topic}")
                return repo.full_name
    log.warning(f"No repo found for process number {process_number} in org {_GITHUB_ORG}")
    return None


def clone_repo(repo_full_name: str, target_dir: str) -> str:
    """Clone the repo into target_dir and return the path."""
    import git
    clone_url = f"https://{_GITHUB_TOKEN}@github.com/{repo_full_name}.git"
    log.info(f"Cloning {repo_full_name} into {target_dir}")
    git.Repo.clone_from(clone_url, target_dir)
    return target_dir


def create_pull_request(
    repo_full_name: str,
    branch_name: str,
    title: str,
    body: str,
    base_branch: str = "main",
) -> str:
    """Create a PR on the repo and return its URL."""
    g = _get_client()
    repo = g.get_repo(repo_full_name)
    pr = repo.create_pull(
        title=title,
        body=body,
        head=branch_name,
        base=base_branch,
    )
    log.info(f"PR created: {pr.html_url}")
    return pr.html_url


def push_branch(local_repo_path: str, branch_name: str, commit_message: str) -> None:
    """Stage all changes, commit, and push the given branch."""
    import git
    repo = git.Repo(local_repo_path)
    repo.git.checkout("-b", branch_name)
    repo.git.add(A=True)
    repo.index.commit(commit_message)
    origin = repo.remote(name="origin")
    origin.push(refspec=f"{branch_name}:{branch_name}")
    log.info(f"Pushed branch {branch_name} to origin")
