"""
GitHub client for SpectreCodingAgent.
Handles repo discovery, duplicate detection, XAML fetching via API, branch/commit/PR operations.
"""
import base64
import os
import re
import time
from typing import Optional
from github import Auth, Github, GithubException

try:
    from .logger import get_logger
except ImportError:
    from logger import get_logger

log = get_logger("spectre.github")

_GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
_GITHUB_ORG = os.getenv("GITHUB_ORG", "UipathAgentHackNithin")

_RETRY_STATUSES = {500, 502, 503, 504}
_MAX_RETRIES = 3
_DUPLICATE_CHECK_LIMIT = 50


def _get_client() -> Github:
    if not _GITHUB_TOKEN:
        raise ValueError("GITHUB_TOKEN environment variable is not set")
    return Github(auth=Auth.Token(_GITHUB_TOKEN))


def _retry(fn, *args, **kwargs):
    """Retry fn on transient GitHub errors (5xx / connection reset)."""
    delay = 1.0
    for attempt in range(_MAX_RETRIES):
        try:
            return fn(*args, **kwargs)
        except GithubException as exc:
            if exc.status in _RETRY_STATUSES and attempt < _MAX_RETRIES - 1:
                log.warning(f"GitHub {exc.status} — retrying in {delay}s ({attempt + 1}/{_MAX_RETRIES})")
                time.sleep(delay)
                delay *= 2
            else:
                raise
        except Exception:
            raise


def find_repo_by_process(process_name: str) -> Optional[str]:
    """Find GitHub repo by searching for a topic matching the process number."""
    match = re.search(r"\b(\d{3,})\b", process_name)
    if not match:
        log.warning(f"No numeric process ID found in: {process_name}")
        return None
    process_number = match.group(1)

    try:
        g = _get_client()
    except ValueError as e:
        log.error(f"GitHub client init failed: {e}")
        return None

    results = g.search_repositories(f"org:{_GITHUB_ORG} topic:{process_number}")
    for repo in results:
        log.info(f"Found repo {repo.full_name} via topic search for {process_number}")
        return repo.full_name
    log.warning(f"No repo found for process number {process_number}")
    return None


def check_duplicate(repo_full_name: str, transaction_id: str) -> Optional[str]:
    """Check if an open PR or issue already exists for this transaction_id."""
    g = _get_client()
    repo = g.get_repo(repo_full_name)

    for i, pr in enumerate(repo.get_pulls(state="open")):
        if i >= _DUPLICATE_CHECK_LIMIT:
            break
        if transaction_id in pr.title:
            log.info(f"Duplicate PR found: {pr.html_url}")
            return pr.html_url

    for i, issue in enumerate(repo.get_issues(state="open")):
        if i >= _DUPLICATE_CHECK_LIMIT:
            break
        if transaction_id in issue.title:
            log.info(f"Duplicate issue found: {issue.html_url}")
            return issue.html_url

    return None


def get_codeowner(repo_full_name: str) -> Optional[str]:
    """Read CODEOWNERS and return the first GitHub username."""
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
                match = re.search(r"@([\w-]+)", line)
                if match:
                    return match.group(1)
        except GithubException:
            continue
    return None


def fetch_xaml_listing(repo_full_name: str, ref: str = "main") -> list[dict]:
    """Return [{path, size}] for all .xaml files in the repo via the Git tree API."""
    g = _get_client()
    repo = g.get_repo(repo_full_name)
    try:
        tree = repo.get_git_tree(ref, recursive=True)
    except GithubException:
        tree = repo.get_git_tree("master", recursive=True)

    results = []
    for item in tree.tree:
        if item.path.endswith(".xaml"):
            results.append({"path": item.path, "size": item.size or 0})
    log.info(f"fetch_xaml_listing: {len(results)} XAML files in {repo_full_name}")
    return results


def fetch_xaml_contents(repo_full_name: str, paths: list[str], ref: str = "main") -> dict[str, str]:
    """Fetch the content of specific XAML files. Returns {path: content}."""
    g = _get_client()
    repo = g.get_repo(repo_full_name)
    result = {}
    for path in paths:
        try:
            file_content = repo.get_contents(path, ref=ref)
            decoded = base64.b64decode(file_content.content).decode("utf-8")
            result[path] = decoded
            log.info(f"Fetched {path} ({len(decoded)} chars)")
        except GithubException as exc:
            log.warning(f"Could not fetch {path}: {exc}")
    return result


def ensure_branch(repo_full_name: str, branch_name: str, base_branch: str = "main") -> None:
    """Create branch from base if it doesn't already exist."""
    g = _get_client()
    repo = g.get_repo(repo_full_name)
    try:
        repo.get_branch(branch_name)
        log.info(f"Branch {branch_name} already exists")
    except GithubException:
        base_sha = repo.get_branch(base_branch).commit.sha
        repo.create_git_ref(ref=f"refs/heads/{branch_name}", sha=base_sha)
        log.info(f"Created branch {branch_name} from {base_branch}")


def commit_file_to_branch(
    repo_full_name: str,
    branch_name: str,
    file_path: str,
    new_content: str,
    commit_message: str,
) -> None:
    """Update a file on the branch via the Contents API."""
    g = _get_client()
    repo = g.get_repo(repo_full_name)
    try:
        existing = repo.get_contents(file_path, ref=branch_name)
        repo.update_file(
            path=file_path,
            message=commit_message,
            content=new_content,
            sha=existing.sha,
            branch=branch_name,
        )
        log.info(f"Updated {file_path} on {branch_name}")
    except GithubException as exc:
        log.warning(f"commit_file_to_branch failed for {file_path}: {exc}")
        raise


def _commit_report(repo_full_name: str, branch_name: str, input, fix_result: dict) -> None:
    """Commit a report-only placeholder file when no patch was applied."""
    report_path = f".spectre/reports/{input.transaction_id}.md"
    content = (
        f"# SpectreAI Report — {input.transaction_id}\n\n"
        f"**Process:** {input.process_name}\n\n"
        f"**Diagnosis:** {input.diagnosis}\n\n"
        f"**Recommended action:** {input.recommended_action}\n\n"
        f"**Analysis:** {fix_result.get('explanation', '')}\n"
    )
    commit_msg = f"[SpectreAI] Diagnosis report for {input.transaction_id}"
    try:
        commit_file_to_branch(repo_full_name, branch_name, report_path, content, commit_msg)
    except GithubException as exc:
        log.warning(f"Could not commit report file: {exc}")


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
    def _do_create():
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

    return _retry(_do_create)
