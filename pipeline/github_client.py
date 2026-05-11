"""
pipeline/github_client.py

GitHub integration for the Zero Human Touch Pipeline.

Uses GitPython for local git operations and the GitHub REST API for PR creation.

Reads configuration from environment variables:
    GITHUB_TOKEN  — Personal access token or fine-grained token with repo scope
    GITHUB_REPO   — Repository in "owner/repo" format, e.g. "acme/my-app"
"""

import os
import re
import shutil
from typing import Dict

import git
import requests

from pipeline.logger import get_logger

logger = get_logger(__name__)

# Files / directories to skip when copying the app into the repo.
_SKIP_NAMES = {"__tests__", "node_modules", "package.json", "package-lock.json", "test-results.txt"}


def _sanitize_slug(text: str, max_length: int = 40) -> str:
    """Convert arbitrary text to a URL-safe branch-name slug.

    Args:
        text:       Input string (e.g. story summary).
        max_length: Maximum number of characters in the result.

    Returns:
        Lowercase slug with spaces converted to hyphens and non-alphanumeric
        characters removed, truncated to ``max_length``.
    """
    slug = text.lower()
    slug = slug.replace(" ", "-")
    slug = re.sub(r"[^a-z0-9\-]", "", slug)
    slug = re.sub(r"-{2,}", "-", slug)  # Collapse consecutive hyphens.
    slug = slug.strip("-")
    return slug[:max_length]


def _copy_app_to_repo(app_dir: str, repo_root: str) -> None:
    """Recursively copy app files into the repository root.

    Entries whose top-level name appears in ``_SKIP_NAMES`` are skipped.

    Args:
        app_dir:   Absolute path to the built app directory.
        repo_root: Absolute path to the cloned repository root.
    """
    for entry in os.scandir(app_dir):
        if entry.name in _SKIP_NAMES:
            logger.debug("Skipping %s", entry.name)
            continue

        dest = os.path.join(repo_root, entry.name)
        if entry.is_dir(follow_symlinks=False):
            shutil.copytree(entry.path, dest, dirs_exist_ok=True)
            logger.debug("Copied directory %s → %s", entry.path, dest)
        else:
            shutil.copy2(entry.path, dest)
            logger.debug("Copied file %s → %s", entry.path, dest)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def push_and_open_pr(issue_key: str, summary: str, app_dir: str) -> Dict[str, object]:
    """Clone the repo, commit the built app on a new branch, open a PR.

    Args:
        issue_key: Jira issue key, e.g. ``ZHTP-42``.
        summary:   Issue summary used to build a human-readable branch name.
        app_dir:   Absolute path to the directory containing the built app files.

    Returns:
        A dict with keys ``pr_url``, ``pr_number``, and ``branch``.
    """
    token = os.environ["GITHUB_TOKEN"]
    repo_slug = os.environ["GITHUB_REPO"]  # e.g. "owner/repo"
    owner, repo_name = repo_slug.split("/", 1)

    slug = _sanitize_slug(summary)
    branch_name = f"feature/{issue_key}-{slug}"
    clone_path = f"/tmp/zhtp-{issue_key}"

    # Build authenticated clone URL.
    clone_url = f"https://{token}@github.com/{owner}/{repo_name}.git"
    logger.info("Cloning %s to %s (depth=1)…", repo_slug, clone_path)

    # Remove any leftover clone from a previous run.
    if os.path.exists(clone_path):
        shutil.rmtree(clone_path)

    repo = git.Repo.clone_from(clone_url, clone_path, depth=1)
    logger.info("Clone complete — HEAD: %s", repo.head.commit.hexsha[:8])

    # Configure committer identity on this repo.
    with repo.config_writer() as cfg:
        cfg.set_value("user", "name", "Pipeline Bot")
        cfg.set_value("user", "email", "pipeline-bot@zero-human-touch.io")

    # Create and check out the feature branch.
    new_branch = repo.create_head(branch_name)
    new_branch.checkout()
    logger.info("Created and checked out branch: %s", branch_name)

    # Copy app files into the repo root.
    _copy_app_to_repo(app_dir, clone_path)

    # Stage everything.
    repo.git.add(A=True)
    changed_files = [item.a_path for item in repo.index.diff("HEAD")]
    new_files = repo.untracked_files
    logger.info(
        "Staging complete — %d changed file(s), %d new file(s)",
        len(changed_files),
        len(new_files),
    )

    commit_message = (
        f"feat({issue_key}): auto-generated app\n\n"
        "Built by Zero Human Touch Pipeline"
    )
    repo.index.commit(commit_message)
    logger.info("Committed: %s", commit_message.splitlines()[0])

    # Push to origin.
    origin = repo.remote("origin")
    push_infos = origin.push(refspec=f"{branch_name}:{branch_name}", set_upstream=True)
    for info in push_infos:
        logger.info("Push result: %s — flags=%s", info.summary, info.flags)

    # Open a pull request via GitHub REST API.
    api_url = f"https://api.github.com/repos/{owner}/{repo_name}/pulls"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    pr_body_text = (
        f"Auto-generated by Zero Human Touch Pipeline\n\nJira story: {issue_key}"
    )
    pr_payload = {
        "title": f"[{issue_key}] {summary}",
        "head": branch_name,
        "base": "main",
        "body": pr_body_text,
    }

    logger.info("Opening PR: '%s' → main", branch_name)
    pr_response = requests.post(api_url, json=pr_payload, headers=headers)

    if pr_response.status_code == 422:
        # Repository may use 'master' as its default branch.
        logger.warning(
            "PR creation to 'main' returned 422; retrying with 'master'…"
        )
        pr_payload["base"] = "master"
        pr_response = requests.post(api_url, json=pr_payload, headers=headers)

    pr_response.raise_for_status()
    pr_data = pr_response.json()
    pr_url = pr_data["html_url"]
    pr_number = pr_data["number"]
    logger.info("PR #%d opened: %s", pr_number, pr_url)

    # Clean up the temp clone on success.
    try:
        shutil.rmtree(clone_path)
        logger.debug("Removed temp clone at %s", clone_path)
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("Could not remove temp clone %s: %s", clone_path, exc)

    return {
        "pr_url": pr_url,
        "pr_number": pr_number,
        "branch": branch_name,
    }
