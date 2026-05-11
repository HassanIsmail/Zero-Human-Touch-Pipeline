#!/usr/bin/env python3
"""Zero Human Touch Pipeline — main entry point.

Polls Jira every 5 minutes for new stories labelled 'ai-ready', then:
  1. Downloads requirements.md from the issue attachment.
  2. Builds a web app with Claude AI.
  3. Runs Jest unit tests (with auto-fix iterations).
  4. Pushes the app to GitHub and opens a pull request.
  5. Deploys the branch to Vercel (preview) and waits for it to be ready.
  6. Runs Playwright QA against the live deployment.
  7. Emails the QA report.
  8. Transitions the Jira story to Done (or Bug Reported on partial/failure).
"""

import os
import time
import traceback

import schedule
from dotenv import load_dotenv

from pipeline.logger import get_logger
from pipeline import builder
from pipeline import test_runner
from pipeline import github_client
from pipeline.vercel_client import VercelClient
from pipeline import qa_agent
from pipeline import email_client
from pipeline.jira_client import JiraClient

logger = get_logger(__name__)

_POLL_INTERVAL_MINUTES = 5


# ---------------------------------------------------------------------------
# Pipeline logic
# ---------------------------------------------------------------------------


def _extract_overall_status(bug_report_content: str) -> str:
    """Parse the overall status from a bug report markdown file.

    Looks for the line ``**Overall status:** PASS / PARTIAL / FAIL``.

    Args:
        bug_report_content: The full text of bug-report.md.

    Returns:
        One of ``"PASS"``, ``"PARTIAL"``, or ``"FAIL"``.
        Defaults to ``"FAIL"`` if the line is not found.
    """
    for line in bug_report_content.splitlines():
        if "**Overall status:**" in line:
            upper = line.upper()
            if "PASS" in upper and "PARTIAL" not in upper:
                return "PASS"
            if "PARTIAL" in upper:
                return "PARTIAL"
            return "FAIL"
    logger.warning("Could not find '**Overall status:**' in bug report — defaulting to FAIL.")
    return "FAIL"


def _process_story(jira: JiraClient, issue: dict) -> None:
    """Run the full pipeline for a single Jira story.

    Args:
        jira:  Initialised JiraClient instance.
        issue: Raw Jira issue dict from ``get_new_stories()``.
    """
    issue_key: str = issue["key"]
    fields: dict = issue.get("fields", {})
    summary: str = fields.get("summary", "(no summary)")

    logger.info("=" * 70)
    logger.info("Processing story: %s — %s", issue_key, summary)
    logger.info("=" * 70)

    # ------------------------------------------------------------------
    # Stage 1a — Download requirements
    # ------------------------------------------------------------------
    requirements = jira.download_requirements(issue_key)
    if not requirements:
        logger.warning("%s has no requirements.md attachment — skipping.", issue_key)
        jira.add_comment(issue_key, "No requirements.md attachment found — story skipped by pipeline.")
        return

    # ------------------------------------------------------------------
    # Stage 1b — Transition to In Progress
    # ------------------------------------------------------------------
    ok = jira.transition_issue(issue_key, "In Progress")
    if not ok:
        logger.warning("Could not transition %s to In Progress — continuing anyway.", issue_key)

    # ------------------------------------------------------------------
    # Create per-issue workspace
    # ------------------------------------------------------------------
    project_root = os.path.dirname(os.path.abspath(__file__))
    workspace_dir = os.path.join(project_root, "workspace", issue_key)
    os.makedirs(workspace_dir, exist_ok=True)
    logger.info("Workspace: %s", workspace_dir)

    # ------------------------------------------------------------------
    # Wrap remaining stages so errors are caught, reported, and the story
    # is moved back to To Do for a future retry.
    # ------------------------------------------------------------------
    try:
        _run_all_stages(jira, issue_key, summary, requirements, workspace_dir)
    except Exception as exc:  # pylint: disable=broad-except
        tb = traceback.format_exc()
        logger.error("Unhandled exception for %s:\n%s", issue_key, tb)

        error_comment = (
            f"Pipeline error for {issue_key}:\n\n"
            f"{type(exc).__name__}: {exc}\n\n"
            "The story has been returned to To Do for retry."
        )
        jira.add_comment(issue_key, error_comment)

        # Try to move back to To Do so it will be picked up next poll.
        reverted = jira.transition_issue(issue_key, "To Do")
        if not reverted:
            # Fall back to whatever state signals an error.
            jira.transition_issue(issue_key, "Error")


def _run_all_stages(
    jira: JiraClient,
    issue_key: str,
    summary: str,
    requirements: str,
    workspace_dir: str,
) -> None:
    """Execute stages 2 through 8 for a single story.

    Args:
        jira:          Initialised JiraClient.
        issue_key:     Jira issue key.
        summary:       Issue summary.
        requirements:  Contents of requirements.md.
        workspace_dir: Absolute path to per-issue workspace.
    """
    # ------------------------------------------------------------------
    # Stage 2 — Build app with Claude
    # ------------------------------------------------------------------
    logger.info("[%s] Stage 2 — Building app with Claude…", issue_key)
    app_files = builder.build_app(requirements, workspace_dir)
    logger.info("[%s] Build complete — %d file(s) generated.", issue_key, len(app_files))

    app_dir = os.path.join(workspace_dir, "app")

    # ------------------------------------------------------------------
    # Stage 3 — Jest unit tests
    # ------------------------------------------------------------------
    logger.info("[%s] Stage 3 — Running Jest tests…", issue_key)
    tests_passed, test_output = test_runner.write_and_run_tests(
        requirements, app_files, workspace_dir
    )
    if tests_passed:
        logger.info("[%s] Tests PASSED.", issue_key)
    else:
        logger.warning(
            "[%s] Tests FAILED — proceeding to GitHub anyway (test failures noted).",
            issue_key,
        )

    # ------------------------------------------------------------------
    # Stage 4 — Push to GitHub and open PR
    # ------------------------------------------------------------------
    logger.info("[%s] Stage 4 — Pushing to GitHub…", issue_key)
    gh_result = github_client.push_and_open_pr(issue_key, summary, app_dir)
    pr_url: str = gh_result["pr_url"]
    branch: str = gh_result["branch"]
    logger.info("[%s] PR opened: %s", issue_key, pr_url)
    jira.add_comment(issue_key, f"GitHub PR opened: {pr_url}")

    # ------------------------------------------------------------------
    # Stage 5 — Deploy to Vercel
    # ------------------------------------------------------------------
    logger.info("[%s] Stage 5 — Deploying to Vercel…", issue_key)
    vercel = VercelClient()
    deployment = vercel.deploy_branch(branch, issue_key)
    deployment_id: str = deployment["id"]

    logger.info("[%s] Waiting for deployment %s to be ready…", issue_key, deployment_id)
    deployed_url = vercel.wait_for_ready(deployment_id)
    logger.info("[%s] Deployment ready at %s", issue_key, deployed_url)

    healthy = vercel.health_check(deployed_url)
    if not healthy:
        logger.warning("[%s] Health check failed for %s", issue_key, deployed_url)

    jira.add_comment(issue_key, f"Deployed to: {deployed_url}")

    # ------------------------------------------------------------------
    # Stage 6 — QA with Playwright
    # ------------------------------------------------------------------
    logger.info("[%s] Stage 6 — Running Playwright QA at %s…", issue_key, deployed_url)
    bug_report_path, screenshots = qa_agent.run_qa(
        deployed_url, requirements, issue_key, workspace_dir
    )

    # Read the report to extract overall status.
    try:
        with open(bug_report_path, "r", encoding="utf-8") as fh:
            bug_report_content = fh.read()
    except OSError as exc:
        logger.error("[%s] Could not read bug report: %s", issue_key, exc)
        bug_report_content = ""

    overall_status = _extract_overall_status(bug_report_content)
    logger.info("[%s] QA overall status: %s", issue_key, overall_status)

    # ------------------------------------------------------------------
    # Stage 7 — Email report
    # ------------------------------------------------------------------
    logger.info("[%s] Stage 7 — Sending QA report email…", issue_key)
    email_client.send_report(issue_key, bug_report_path, screenshots, overall_status)

    # ------------------------------------------------------------------
    # Stage 8 — Close Jira story
    # ------------------------------------------------------------------
    logger.info("[%s] Stage 8 — Closing Jira story (status=%s)…", issue_key, overall_status)
    if overall_status == "PASS":
        transitioned = jira.transition_issue(issue_key, "Done")
        if not transitioned:
            logger.warning("[%s] Could not transition to Done.", issue_key)
        jira.add_comment(
            issue_key,
            f"All tests passed. App deployed at {deployed_url}",
        )
    else:
        # Try Bug Reported → In Review → Done, in order of preference.
        fallback_statuses = ["Bug Reported", "In Review", "Done"]
        transitioned = False
        for status in fallback_statuses:
            transitioned = jira.transition_issue(issue_key, status)
            if transitioned:
                logger.info("[%s] Transitioned to '%s'.", issue_key, status)
                break

        if not transitioned:
            logger.warning(
                "[%s] Could not find a suitable transition for status=%s.",
                issue_key,
                overall_status,
            )

        # Add the full bug report as a comment so it is visible in Jira.
        comment_text = (
            f"QA status: {overall_status}\n\n"
            f"Deployed at: {deployed_url}\n\n"
            f"--- Bug Report ---\n{bug_report_content[:10000]}"
        )
        jira.add_comment(issue_key, comment_text)

    logger.info("[%s] Pipeline complete.", issue_key)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def run_pipeline() -> None:
    """Fetch new Jira stories and process each one through the full pipeline."""
    logger.info("--- Pipeline run starting ---")
    try:
        jira = JiraClient()
        stories = jira.get_new_stories()
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Failed to fetch Jira stories: %s", exc, exc_info=True)
        return

    if not stories:
        logger.info("No new stories found. Waiting for next poll.")
        return

    logger.info("Found %d story/stories to process.", len(stories))

    for issue in stories:
        try:
            _process_story(jira, issue)
        except Exception as exc:  # pylint: disable=broad-except
            # This outer guard should not normally be reached because
            # _process_story has its own catch-all, but it protects the loop.
            logger.error(
                "Unexpected error processing %s: %s",
                issue.get("key", "UNKNOWN"),
                exc,
                exc_info=True,
            )

    logger.info("--- Pipeline run complete ---")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Load config, run pipeline immediately, then poll every 5 minutes."""
    # Load .env file if present.
    load_dotenv()

    logger.info(
        "Zero Human Touch Pipeline starting — polling every %d minutes.",
        _POLL_INTERVAL_MINUTES,
    )

    # Run immediately on startup.
    run_pipeline()

    # Schedule subsequent runs.
    schedule.every(_POLL_INTERVAL_MINUTES).minutes.do(run_pipeline)
    logger.info("Scheduler active. Press Ctrl+C to stop.")

    try:
        while True:
            schedule.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        logger.info("Pipeline stopped by user (KeyboardInterrupt).")


if __name__ == "__main__":
    main()
