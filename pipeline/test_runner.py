"""
pipeline/test_runner.py

Generates and runs Jest unit tests for apps built by the Claude AI builder.

The module:
1. Asks Claude (via the `claude` CLI) to write a Jest test suite covering
   every acceptance criterion.
2. Installs Node dependencies (jest, jest-environment-jsdom, @testing-library/*).
3. Runs the test suite via ``npm test``.
4. On failure, calls builder.fix_app() to patch the app, then retries.
5. Saves the final test output to ``workspace_dir/test-results.txt``.

No ANTHROPIC_API_KEY needed — uses the logged-in Claude Code session.
"""

import json
import os
import subprocess
from typing import Dict, Tuple

from pipeline.logger import get_logger
from pipeline import builder as app_builder

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MODEL = "claude-sonnet-4-6"
_MAX_ITERATIONS = 3

_PACKAGE_JSON: dict = {
    "name": "app-tests",
    "private": True,
    "scripts": {"test": "jest --no-coverage --forceExit 2>&1"},
    "jest": {
        "testEnvironment": "jsdom",
        "testMatch": ["**/__tests__/**/*.test.js"],
        "moduleDirectories": ["node_modules"],
    },
    "devDependencies": {
        "jest": "^29.7.0",
        "jest-environment-jsdom": "^29.7.0",
        "@testing-library/dom": "^10.0.0",
        "@testing-library/jest-dom": "^6.4.0",
    },
}

_TEST_SYSTEM_PROMPT = """You are an expert JavaScript test engineer.
Write comprehensive Jest tests that cover every acceptance criterion listed in the requirements.
Rules:
- Use jest-environment-jsdom.
- Load the HTML by reading the file with fs.readFileSync and setting document.body.innerHTML.
- Require the app module via require('../app/app.js').
- Test every acceptance criterion from the requirements.
- Use descriptive test names.
- Do NOT include markdown fences (```) in your output — return raw JavaScript only.
"""


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _generate_test_file(requirements: str, app_files: Dict[str, str]) -> str:
    """Ask Claude to generate a Jest test file.

    Args:
        requirements: Full requirements document text.
        app_files:    Current app files dict (relative_path → content).

    Returns:
        Raw JavaScript source of the test file (no markdown fences).
    """
    files_section = "\n\n".join(
        f"=== {path} ===\n{content}" for path, content in app_files.items()
    )

    user_message = (
        "Write a complete Jest test file for the following web application.\n"
        "The test file will be saved to __tests__/app.test.js\n\n"
        "=== REQUIREMENTS ===\n"
        f"{requirements}\n"
        "=== END REQUIREMENTS ===\n\n"
        "=== APP FILES ===\n"
        f"{files_section}\n"
        "=== END APP FILES ===\n\n"
        "Instructions:\n"
        "- At the top of the file, use: const fs = require('fs');\n"
        "- Load HTML with: document.body.innerHTML = fs.readFileSync(__dirname + '/../app/index.html', 'utf8');\n"
        "- Require the app: const app = require('../app/app.js');\n"
        "- Write one describe block per acceptance criterion.\n"
        "- Return ONLY raw JavaScript — no markdown, no code fences.\n"
    )

    full_prompt = f"{_TEST_SYSTEM_PROMPT}\n\n---\n\n{user_message}"
    logger.info("Calling claude CLI to generate Jest test file…")

    result = subprocess.run(
        ["claude", "-p", "--model", _MODEL, "--tools", ""],
        input=full_prompt,
        capture_output=True,
        text=True,
        timeout=600,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"claude CLI failed generating tests (code {result.returncode}):\n{result.stderr}"
        )

    raw = result.stdout.strip()

    # If response contains a markdown code fence, extract its contents.
    if "```" in raw:
        # Find the first ``` block and extract its content.
        start_idx = raw.find("```")
        end_idx = raw.rfind("```")
        if start_idx != end_idx:
            block = raw[start_idx:end_idx + 3]
            # Strip opening fence line (```javascript or ```)
            block = block.split("\n", 1)[1] if "\n" in block else block[3:]
            # Strip closing fence
            if block.endswith("```"):
                block = block[: block.rfind("```")]
            raw = block.strip()
    else:
        # Strip any non-JS preamble lines (e.g. "Writing the file directly.").
        js_starters = (
            "/**", "//", "'use strict'", '"use strict"',
            "const ", "var ", "let ", "require(", "describe(",
            "it(", "test(", "function ", "import ", "module.",
            "beforeEach(", "afterEach(", "beforeAll(", "afterAll(",
        )
        lines = raw.split("\n")
        for i, line in enumerate(lines):
            stripped = line.lstrip()
            if any(stripped.startswith(s) for s in js_starters):
                raw = "\n".join(lines[i:])
                break

    logger.info("Test file generated — %d chars", len(raw))
    return raw.strip()


def _write_workspace_files(
    workspace_dir: str, test_code: str
) -> None:
    """Write package.json and the test file to the workspace."""
    # package.json at workspace root
    pkg_path = os.path.join(workspace_dir, "package.json")
    with open(pkg_path, "w", encoding="utf-8") as fh:
        json.dump(_PACKAGE_JSON, fh, indent=2)
    logger.debug("Wrote %s", pkg_path)

    # Test file
    tests_dir = os.path.join(workspace_dir, "__tests__")
    os.makedirs(tests_dir, exist_ok=True)
    test_path = os.path.join(tests_dir, "app.test.js")
    with open(test_path, "w", encoding="utf-8") as fh:
        fh.write(test_code)
    logger.debug("Wrote %s (%d chars)", test_path, len(test_code))


def _run_npm(workspace_dir: str, args: list) -> subprocess.CompletedProcess:
    """Run an npm command inside workspace_dir."""
    cmd = ["npm"] + args
    logger.info("Running: %s in %s", " ".join(cmd), workspace_dir)
    result = subprocess.run(
        cmd,
        cwd=workspace_dir,
        capture_output=True,
        text=True,
        timeout=120,
    )
    logger.debug("Return code: %d", result.returncode)
    if result.stdout:
        logger.debug("stdout:\n%s", result.stdout[-3000:])
    if result.stderr:
        logger.debug("stderr:\n%s", result.stderr[-2000:])
    return result


def _save_results(workspace_dir: str, output: str) -> None:
    """Persist the final test output to test-results.txt."""
    path = os.path.join(workspace_dir, "test-results.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(output)
    logger.info("Test results saved to %s", path)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def write_and_run_tests(
    requirements: str, app_files: Dict[str, str], workspace_dir: str
) -> Tuple[bool, str]:
    """Generate, run, and optionally fix tests iteratively.

    This function orchestrates the full test loop:
    1. Generate a Jest test file with Claude.
    2. Write package.json + test file.
    3. ``npm install`` then ``npm test``.
    4. If tests fail, call builder.fix_app() and retry (max 3 iterations total).
    5. Save the final output.

    Args:
        requirements:  Full requirements document text.
        app_files:     Initial app files dict (relative_path → content).
        workspace_dir: Absolute path to the per-issue workspace directory.

    Returns:
        A tuple ``(success, output)`` where ``success`` is ``True`` if the
        final test run exited with code 0.
    """
    current_app_files = dict(app_files)
    final_output = ""
    success = False

    for iteration in range(1, _MAX_ITERATIONS + 1):
        logger.info("Test iteration %d / %d", iteration, _MAX_ITERATIONS)

        # Step 1 — generate test file.
        test_code = _generate_test_file(requirements, current_app_files)

        # Step 2 — write files.
        _write_workspace_files(workspace_dir, test_code)

        # Step 3 — install deps (only first time or after fix changes package.json).
        install_result = _run_npm(workspace_dir, ["install"])
        if install_result.returncode != 0:
            logger.error(
                "npm install failed (iteration %d):\n%s\n%s",
                iteration,
                install_result.stdout,
                install_result.stderr,
            )
            # Non-recoverable for this iteration; surface the error.
            final_output = (
                f"npm install failed:\n{install_result.stdout}\n{install_result.stderr}"
            )
            break

        # Step 4 — run tests.
        test_result = _run_npm(workspace_dir, ["test"])
        final_output = test_result.stdout + "\n" + test_result.stderr

        if test_result.returncode == 0:
            logger.info("Tests PASSED on iteration %d", iteration)
            success = True
            break

        logger.warning(
            "Tests FAILED on iteration %d (returncode=%d)",
            iteration,
            test_result.returncode,
        )

        # Step 5 — fix app if we have remaining iterations.
        if iteration < _MAX_ITERATIONS:
            logger.info("Asking Claude to fix app.js based on test failures…")
            failures_excerpt = final_output[-4000:]  # Send last 4 KB of output.
            try:
                current_app_files = app_builder.fix_app(
                    requirements=requirements,
                    app_files=current_app_files,
                    test_failures=failures_excerpt,
                    workspace_dir=workspace_dir,
                )
            except Exception as exc:  # pylint: disable=broad-except
                logger.exception("builder.fix_app raised an exception: %s", exc)
                # Continue loop anyway with existing files.
        else:
            logger.warning(
                "Max iterations (%d) reached — tests still failing.", _MAX_ITERATIONS
            )

    # Step 6 — persist results.
    _save_results(workspace_dir, final_output)

    return success, final_output
