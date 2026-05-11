"""
pipeline/builder.py

Claude AI application builder for the Zero Human Touch Pipeline.

Uses the `claude` CLI (Claude Code) in non-interactive mode (-p) so no
ANTHROPIC_API_KEY is needed — authentication comes from the logged-in
Clustox org account (m.hassan@clustox.com).
"""

import os
import re
import subprocess
from typing import Dict

from pipeline.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MODEL = "claude-sonnet-4-6"

_SYSTEM_PROMPT = """You are an expert web developer.
Your job is to produce complete, fully working web applications from requirements.
Rules:
- Do NOT ask for clarification — always produce the complete app.
- Output EVERY file using this exact format and nothing else outside of it:
  <FILE path="filename.ext">
  ...file content...
  </FILE>
- Put JavaScript in a separate file called app.js for testability.
- In app.js, expose all key functions on the window object AND via CommonJS
  module.exports using a guard like:
    if (typeof module !== 'undefined' && module.exports) {
      module.exports = { functionName, ... };
    }
- Always create a minimal vercel.json containing exactly: {"version": 2}
- Always include a style.css file with tasteful, professional styling.
- The app must be fully self-contained (no CDN imports that might be blocked).
"""

_FILE_PATTERN = re.compile(r'<FILE path="([^"]+)">(.*?)</FILE>', re.DOTALL)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_files(files: Dict[str, str], workspace_dir: str) -> None:
    """Write a mapping of {relative_path: content} into ``workspace_dir/app/``."""
    app_dir = os.path.join(workspace_dir, "app")
    for rel_path, content in files.items():
        dest = os.path.join(app_dir, rel_path)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(content)
        logger.debug("Wrote %s (%d bytes)", dest, len(content))


def _parse_files(response_text: str) -> Dict[str, str]:
    """Extract <FILE path="...">...</FILE> blocks from Claude's response.

    Args:
        response_text: The raw text returned by Claude.

    Returns:
        A dict mapping relative file path → file content.
    """
    matches = _FILE_PATTERN.findall(response_text)
    if not matches:
        logger.warning("No <FILE ...> blocks found in Claude response.")
    files = {}
    for path, content in matches:
        # Strip a single leading newline that Claude typically inserts.
        files[path] = content.lstrip("\n")
    return files


def _call_claude(system: str, user_message: str) -> str:
    """Run the claude CLI in non-interactive mode and return the response.

    Pipes ``system + user_message`` via stdin to ``claude -p``.
    Authentication comes from the logged-in Claude Code session
    (no API key required).

    Args:
        system:       System instructions prepended to the user message.
        user_message: The actual task/request for Claude.

    Returns:
        The assistant's reply as a plain string.
    """
    logger.info("Calling claude CLI (model=%s)…", _MODEL)

    result = subprocess.run(
        [
            "/home/clustox/.nvm/versions/node/v24.14.1/bin/claude",
            "-p",
            "--bare",
            "--model",
            _MODEL,
            "--system-prompt",
            system,
            "--dangerously-skip-permissions",
            user_message,
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=600,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"claude CLI exited with code {result.returncode}:\n"
            f"STDERR: {result.stderr}\nSTDOUT: {result.stdout}"
        )

    response_text = result.stdout.strip()
    logger.info("Claude CLI responded — %d chars", len(response_text))
    return response_text


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_app(requirements: str, workspace_dir: str) -> Dict[str, str]:
    """Ask Claude to build a complete web app from requirements.

    Files are written to ``workspace_dir/app/``.

    Args:
        requirements: Full text of the requirements document.
        workspace_dir: Absolute path to the per-issue workspace directory.

    Returns:
        A dict mapping relative path → file content for every generated file.
    """
    logger.info("Building app in workspace: %s", workspace_dir)

    user_message = (
        "Build a complete web application for the following requirements.\n"
        "Return ALL files using the <FILE path=\"...\"> format.\n\n"
        "=== REQUIREMENTS ===\n"
        f"{requirements}\n"
        "=== END REQUIREMENTS ===\n\n"
        "Required files (minimum):\n"
        "  - index.html\n"
        "  - app.js  (all logic here; expose functions on window and module.exports)\n"
        "  - style.css\n"
        "  - vercel.json  (must contain exactly: {\"version\": 2})\n\n"
        "Remember: the JavaScript MUST use the module.exports guard pattern so it "
        "can be required by Jest tests."
    )

    response_text = _call_claude(_SYSTEM_PROMPT, user_message)
    files = _parse_files(response_text)

    logger.info("Claude generated %d file(s): %s", len(files), list(files.keys()))

    # Ensure the app directory exists before writing.
    os.makedirs(os.path.join(workspace_dir, "app"), exist_ok=True)
    _write_files(files, workspace_dir)

    return files


def fix_app(
    requirements: str,
    app_files: Dict[str, str],
    test_failures: str,
    workspace_dir: str,
) -> Dict[str, str]:
    """Ask Claude to fix app.js (and any other files) based on test failures.

    The fixed files are written back to ``workspace_dir/app/``, overwriting
    the previous versions.

    Args:
        requirements:  Full requirements text.
        app_files:     Dict of current file contents (relative_path → content).
        test_failures: Jest output / error messages from the failed test run.
        workspace_dir: Absolute path to the per-issue workspace directory.

    Returns:
        Updated dict of relative path → file content.
    """
    logger.info("Fixing app — test failures:\n%s", test_failures[:1000])

    # Build a compact representation of the current files.
    files_section = "\n\n".join(
        f'=== {path} ===\n{content}' for path, content in app_files.items()
    )

    user_message = (
        "The following web application has failing Jest tests.\n"
        "Fix the app.js logic (and any other files necessary) so all tests pass.\n"
        "Return ALL modified files using the <FILE path=\"...\"> format.\n\n"
        "=== ORIGINAL REQUIREMENTS ===\n"
        f"{requirements}\n"
        "=== END REQUIREMENTS ===\n\n"
        "=== CURRENT APP FILES ===\n"
        f"{files_section}\n"
        "=== END APP FILES ===\n\n"
        "=== TEST FAILURES ===\n"
        f"{test_failures}\n"
        "=== END TEST FAILURES ===\n\n"
        "Fix the JavaScript logic. Keep the module.exports guard pattern so Jest "
        "can import the functions. Return every file you changed (including unchanged "
        "files if needed for context)."
    )

    response_text = _call_claude(_SYSTEM_PROMPT, user_message)
    fixed_files = _parse_files(response_text)

    # Merge: keep existing files, overlay with anything Claude returned.
    merged = dict(app_files)
    merged.update(fixed_files)

    logger.info(
        "Claude fixed %d file(s): %s", len(fixed_files), list(fixed_files.keys())
    )

    _write_files(merged, workspace_dir)
    return merged
