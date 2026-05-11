"""
pipeline/qa_agent.py

Playwright-based QA agent for the Zero Human Touch Pipeline.

Uses the synchronous Playwright API to exercise a deployed app and then
delegates report synthesis to the `claude` CLI in non-interactive mode.

The agent:
1. Takes an initial screenshot of the deployed URL.
2. Attempts common interactions (add item, mark complete, delete, check counter,
   test localStorage persistence) using try/except for graceful degradation.
3. Captures browser console errors throughout.
4. Asks Claude to produce a structured bug report from the evidence gathered.
5. Writes the report to ``workspace_dir/bug-report.md``.

No ANTHROPIC_API_KEY needed — uses the logged-in Claude Code session.
"""

import os
import subprocess
from datetime import datetime, timezone
from typing import List, Tuple

from playwright.sync_api import sync_playwright, Page, ConsoleMessage

from pipeline.logger import get_logger

logger = get_logger(__name__)

_MODEL = "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _take_screenshot(page: Page, screenshots_dir: str, name: str) -> str:
    """Take a screenshot and return its absolute path."""
    path = os.path.join(screenshots_dir, name)
    page.screenshot(path=path, full_page=True)
    logger.debug("Screenshot saved: %s", path)
    return path


def _attempt_add_item(page: Page, screenshots_dir: str, screenshots: List[str]) -> str:
    """Try to find a text input and submit it to add a new item."""
    result_note = ""
    try:
        # Common selectors for text inputs.
        input_el = page.query_selector('input[type="text"], input:not([type])')
        if input_el:
            input_el.fill("Test item from QA bot")
            # Try pressing Enter or clicking a nearby submit/add button.
            btn = page.query_selector('button[type="submit"], button:has-text("Add"), button:has-text("add")')
            if btn:
                btn.click()
            else:
                input_el.press("Enter")

            page.wait_for_timeout(500)
            shot = _take_screenshot(page, screenshots_dir, "screenshot-02-add-item.png")
            screenshots.append(shot)
            result_note = "Filled text input with 'Test item from QA bot' and submitted."
            logger.info("Added test item via text input.")
        else:
            result_note = "No text input found on page."
            logger.info("No text input found — skipping add-item test.")
    except Exception as exc:  # pylint: disable=broad-except
        result_note = f"Add-item attempt failed: {exc}"
        logger.warning("Add-item interaction failed: %s", exc)
    return result_note


def _attempt_mark_complete(page: Page, screenshots_dir: str, screenshots: List[str]) -> str:
    """Try to find and click the first checkbox to mark an item complete."""
    result_note = ""
    try:
        checkbox = page.query_selector('input[type="checkbox"]')
        if checkbox:
            checkbox.click()
            page.wait_for_timeout(500)
            shot = _take_screenshot(page, screenshots_dir, "screenshot-03-mark-complete.png")
            screenshots.append(shot)
            result_note = "Clicked first checkbox to mark item as complete."
            logger.info("Marked first checkbox as complete.")
        else:
            result_note = "No checkbox found on page."
            logger.info("No checkbox found — skipping mark-complete test.")
    except Exception as exc:  # pylint: disable=broad-except
        result_note = f"Mark-complete attempt failed: {exc}"
        logger.warning("Mark-complete interaction failed: %s", exc)
    return result_note


def _attempt_delete(page: Page, screenshots_dir: str, screenshots: List[str]) -> str:
    """Try to find and click a delete/remove button."""
    result_note = ""
    try:
        delete_btn = page.query_selector(
            'button:has-text("Delete"), button:has-text("Remove"), '
            'button:has-text("×"), button:has-text("✕"), [data-action="delete"]'
        )
        if delete_btn:
            delete_btn.click()
            page.wait_for_timeout(500)
            shot = _take_screenshot(page, screenshots_dir, "screenshot-04-delete.png")
            screenshots.append(shot)
            result_note = "Clicked delete/remove button."
            logger.info("Clicked delete button.")
        else:
            result_note = "No delete/remove button found on page."
            logger.info("No delete button found — skipping delete test.")
    except Exception as exc:  # pylint: disable=broad-except
        result_note = f"Delete attempt failed: {exc}"
        logger.warning("Delete interaction failed: %s", exc)
    return result_note


def _check_counter(page: Page) -> str:
    """Read visible counter/status text from the page."""
    try:
        # Common patterns for item counters.
        for selector in [
            '[data-testid="counter"]', '.counter', '#counter',
            '[class*="count"]', '[id*="count"]',
        ]:
            el = page.query_selector(selector)
            if el:
                text = el.inner_text().strip()
                logger.info("Counter element found (%s): '%s'", selector, text)
                return f"Counter element text: '{text}'"
        # Fallback — scan for text containing typical counter patterns.
        body_text = page.inner_text("body")
        for line in body_text.splitlines():
            line = line.strip()
            if any(word in line.lower() for word in ("item", "task", "remaining", "left", "total")):
                return f"Possible counter text found: '{line[:120]}'"
        return "No counter element detected."
    except Exception as exc:  # pylint: disable=broad-except
        return f"Counter check failed: {exc}"


def _attempt_persistence(page: Page, url: str, screenshots_dir: str, screenshots: List[str]) -> str:
    """Reload the page and check whether items persist (localStorage)."""
    result_note = ""
    try:
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(500)
        shot = _take_screenshot(page, screenshots_dir, "screenshot-05-reload.png")
        screenshots.append(shot)
        body_text = page.inner_text("body")
        if "Test item from QA bot" in body_text:
            result_note = "Item 'Test item from QA bot' persisted after page reload (localStorage working)."
            logger.info("localStorage persistence confirmed.")
        else:
            result_note = "Added item did NOT persist after page reload (localStorage may not be implemented)."
            logger.info("Item did not persist after reload.")
    except Exception as exc:  # pylint: disable=broad-except
        result_note = f"Persistence check failed: {exc}"
        logger.warning("Persistence check failed: %s", exc)
    return result_note


def _synthesise_report(
    url: str,
    requirements: str,
    issue_key: str,
    interaction_log: List[str],
    console_errors: List[str],
    screenshot_paths: List[str],
    tested_at: str,
) -> str:
    """Ask Claude to write the bug-report.md content from collected evidence.

    Args:
        url:              Deployed app URL.
        requirements:     Full requirements document text.
        issue_key:        Jira issue key.
        interaction_log:  Notes from each automated interaction step.
        console_errors:   Browser console error messages.
        screenshot_paths: Absolute paths of all screenshots taken.
        tested_at:        ISO timestamp of when testing started.

    Returns:
        Markdown content for the bug report.
    """
    screenshot_names = [os.path.basename(p) for p in screenshot_paths]

    log_section = "\n".join(f"- {note}" for note in interaction_log) or "No interactions attempted."
    errors_section = "\n".join(console_errors) if console_errors else "None"
    screenshots_section = "\n".join(f"- {name}" for name in screenshot_names)

    system_prompt = (
        "You are a professional QA engineer.\n"
        "Given test evidence, produce a structured bug report in Markdown.\n"
        "Determine an overall status of PASS, PARTIAL, or FAIL based on the evidence.\n"
        "Be concise but thorough."
    )

    user_message = (
        f"Produce the complete content of a bug-report.md file for Jira issue {issue_key}.\n\n"
        "=== REQUIREMENTS ===\n"
        f"{requirements}\n"
        "=== END REQUIREMENTS ===\n\n"
        "=== AUTOMATED INTERACTION LOG ===\n"
        f"{log_section}\n"
        "=== END LOG ===\n\n"
        "=== BROWSER CONSOLE ERRORS ===\n"
        f"{errors_section}\n"
        "=== END ERRORS ===\n\n"
        "=== SCREENSHOTS TAKEN ===\n"
        f"{screenshots_section}\n"
        "=== END SCREENSHOTS ===\n\n"
        "The report MUST use exactly this structure:\n\n"
        f"# QA Report — {issue_key}\n"
        f"**Deployment URL:** {url}\n"
        f"**Tested at:** {tested_at}\n"
        "**Overall status:** PASS / PARTIAL / FAIL  ← choose one\n\n"
        "## Test Results\n"
        "| Acceptance Criterion | Result | Notes |\n"
        "|---|---|---|\n"
        "| <criterion> | PASS/FAIL | <notes> |\n\n"
        "## Console Errors\n"
        "<list any errors or 'None'>\n\n"
        "## Screenshots\n"
        "<list screenshot filenames>\n\n"
        "## Summary\n"
        "<2-3 sentence overall summary>\n"
    )

    full_prompt = f"{system_prompt}\n\n---\n\n{user_message}"
    logger.info("Calling claude CLI to synthesise QA report for %s…", issue_key)

    result = subprocess.run(
        ["claude", "-p", "--model", _MODEL, "--tools", ""],
        input=full_prompt,
        capture_output=True,
        text=True,
        timeout=600,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"claude CLI failed synthesising report (code {result.returncode}):\n{result.stderr}"
        )

    report_content = result.stdout.strip()
    logger.info("QA report generated — %d chars", len(report_content))
    return report_content


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_qa(
    url: str, requirements: str, issue_key: str, workspace_dir: str
) -> Tuple[str, List[str]]:
    """Run Playwright QA against a deployed app and produce a bug report.

    Args:
        url:           Fully-qualified URL of the deployed app.
        requirements:  Full requirements document text.
        issue_key:     Jira issue key used for report naming.
        workspace_dir: Absolute path to the per-issue workspace directory.

    Returns:
        A tuple ``(bug_report_path, screenshots)`` where:
        - ``bug_report_path`` is the absolute path of the generated report.
        - ``screenshots`` is a list of absolute paths of all PNG files captured.
    """
    screenshots_dir = os.path.join(workspace_dir, "screenshots")
    os.makedirs(screenshots_dir, exist_ok=True)

    console_errors: List[str] = []
    screenshots: List[str] = []
    interaction_log: List[str] = []
    tested_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    logger.info("Starting QA for %s at %s", issue_key, url)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()

        # Capture console errors throughout the session.
        def _on_console(msg: ConsoleMessage) -> None:
            if msg.type in ("error", "warning"):
                entry = f"[{msg.type.upper()}] {msg.text}"
                console_errors.append(entry)
                logger.debug("Console %s: %s", msg.type, msg.text)

        page.on("console", _on_console)

        # --- Step 1: Initial load ---
        try:
            logger.info("Navigating to %s…", url)
            page.goto(url, wait_until="networkidle", timeout=30_000)
            shot = _take_screenshot(page, screenshots_dir, "screenshot-01-initial-load.png")
            screenshots.append(shot)
            interaction_log.append("Navigated to app URL successfully — initial screenshot taken.")
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("Failed to load %s: %s", url, exc)
            interaction_log.append(f"FAILED to load URL: {exc}")
            # Write a minimal failure report and return early.
            report_content = (
                f"# QA Report — {issue_key}\n"
                f"**Deployment URL:** {url}\n"
                f"**Tested at:** {tested_at}\n"
                "**Overall status:** FAIL\n\n"
                f"## Summary\nThe page failed to load: {exc}\n"
            )
            bug_report_path = os.path.join(workspace_dir, "bug-report.md")
            with open(bug_report_path, "w", encoding="utf-8") as fh:
                fh.write(report_content)
            browser.close()
            return bug_report_path, screenshots

        # --- Step 2: Add item ---
        note = _attempt_add_item(page, screenshots_dir, screenshots)
        interaction_log.append(note)

        # --- Step 3: Mark complete ---
        note = _attempt_mark_complete(page, screenshots_dir, screenshots)
        interaction_log.append(note)

        # --- Step 4: Delete item ---
        note = _attempt_delete(page, screenshots_dir, screenshots)
        interaction_log.append(note)

        # --- Step 5: Check counter ---
        note = _check_counter(page)
        interaction_log.append(note)

        # --- Step 6: Test persistence ---
        note = _attempt_persistence(page, url, screenshots_dir, screenshots)
        interaction_log.append(note)

        # Final screenshot after all interactions.
        try:
            shot = _take_screenshot(page, screenshots_dir, "screenshot-06-final.png")
            screenshots.append(shot)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("Final screenshot failed: %s", exc)

        browser.close()

    logger.info(
        "QA interactions complete — %d screenshots, %d console errors",
        len(screenshots),
        len(console_errors),
    )

    # --- Step 7: Ask Claude to synthesise the report ---
    report_content = _synthesise_report(
        url=url,
        requirements=requirements,
        issue_key=issue_key,
        interaction_log=interaction_log,
        console_errors=console_errors,
        screenshot_paths=screenshots,
        tested_at=tested_at,
    )

    # Write the report file.
    bug_report_path = os.path.join(workspace_dir, "bug-report.md")
    with open(bug_report_path, "w", encoding="utf-8") as fh:
        fh.write(report_content)
    logger.info("Bug report written to %s", bug_report_path)

    return bug_report_path, screenshots
