import asyncio
import tempfile
import os
from pydantic import BaseModel

try:
    from .logger import get_logger
    from .github_client import find_repo_by_process, clone_repo, push_branch, create_pull_request
    from .xaml_fixer import apply_fix
except ImportError:
    from logger import get_logger
    from github_client import find_repo_by_process, clone_repo, push_branch, create_pull_request
    from xaml_fixer import apply_fix

log = get_logger("spectre.coding_agent")

_GITHUB_ORG = os.getenv("GITHUB_ORG", "UipathAgentHackNithin")


class FixIn(BaseModel):
    transaction_id: str
    process_name: str
    diagnosis: str
    recommended_action: str
    confidence: str = "Medium"


class FixOut(BaseModel):
    fixed: bool
    pr_url: str
    branch_name: str
    file_changed: str
    fix_description: str
    repo_full_name: str
    message: str


async def fix(input: FixIn) -> FixOut:
    return await _run(input)


async def _run(input: FixIn) -> FixOut:
    log.info(f"SpectreCodingAgent starting — transaction={input.transaction_id} process={input.process_name}")

    # Step 1: Find the GitHub repo for this process
    repo_full_name = find_repo_by_process(input.process_name)
    if not repo_full_name:
        msg = f"No GitHub repo found for process: {input.process_name}"
        log.warning(msg)
        return FixOut(
            fixed=False, pr_url="", branch_name="", file_changed="",
            fix_description="", repo_full_name="", message=msg
        )

    log.info(f"Repo found: {repo_full_name}")

    # Step 2: Clone the repo into a temp directory
    with tempfile.TemporaryDirectory() as tmp_dir:
        clone_repo(repo_full_name, tmp_dir)

        # Step 3: Apply fix
        fix_result = apply_fix(
            repo_path=tmp_dir,
            diagnosis=input.diagnosis,
            recommended_action=input.recommended_action,
            process_name=input.process_name,
            transaction_id=input.transaction_id,
        )

        if not fix_result["fixed"]:
            log.info(f"No code change made: {fix_result['description']}")
            return FixOut(
                fixed=False, pr_url="", branch_name="",
                file_changed=fix_result["file_changed"],
                fix_description=fix_result["description"],
                repo_full_name=repo_full_name,
                message=f"Analysis complete but no automated fix available. {fix_result['description']}"
            )

        # Step 4: Push branch and open PR
        branch_name = f"spectre-fix/{input.transaction_id.lower()}"
        commit_msg = f"[SpectreAI] Auto-fix for {input.transaction_id}: {fix_result['description'][:72]}"
        push_branch(tmp_dir, branch_name, commit_msg)

        pr_title = f"[SpectreAI] Fix for {input.process_name} — {input.transaction_id}"
        pr_body = (
            f"## SpectreAI Automated Fix\n\n"
            f"**Transaction ID:** {input.transaction_id}\n"
            f"**Process:** {input.process_name}\n"
            f"**Confidence:** {input.confidence}\n\n"
            f"### Diagnosis\n{input.diagnosis}\n\n"
            f"### Recommended Action\n{input.recommended_action}\n\n"
            f"### Change Applied\n{fix_result['description']}\n\n"
            f"**File changed:** `{fix_result['file_changed']}`\n\n"
            f"---\n*Opened automatically by SpectreCodingAgent*"
        )
        pr_url = create_pull_request(repo_full_name, branch_name, pr_title, pr_body)

    log.info(f"Done — PR opened at {pr_url}")
    return FixOut(
        fixed=True,
        pr_url=pr_url,
        branch_name=branch_name,
        file_changed=fix_result["file_changed"],
        fix_description=fix_result["description"],
        repo_full_name=repo_full_name,
        message=f"Fix applied and PR opened: {pr_url}"
    )


if __name__ == "__main__":
    result = asyncio.run(fix(FixIn(
        transaction_id="INV-98766",
        process_name="3201 Invoice Processing",
        diagnosis="SAP login failed due to credential timeout",
        recommended_action="Rotate SAP credentials and add retry logic",
        confidence="High",
    )))
    print(result)
