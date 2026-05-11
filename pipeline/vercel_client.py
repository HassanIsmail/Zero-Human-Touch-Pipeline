"""
pipeline/vercel_client.py

Vercel REST API client for the Zero Human Touch Pipeline.

Triggers preview deployments from a GitHub branch and waits for them to be ready.

Reads configuration from environment variables:
    VERCEL_TOKEN        — Vercel personal access token
    VERCEL_PROJECT_ID   — Vercel project ID (prj_...)
    VERCEL_ORG_ID       — Vercel team / org ID (team_...) — optional
    VERCEL_PROJECT_NAME — Human-readable Vercel project name
    GITHUB_REPO         — "owner/repo" GitHub repository linked to the project
"""

import os
import time

import requests

from pipeline.logger import get_logger

logger = get_logger(__name__)

_BASE_URL = "https://api.vercel.com"
_POLL_INTERVAL = 10  # seconds between deployment status polls
_HEALTH_CHECK_RETRIES = 5
_HEALTH_CHECK_DELAY = 5  # seconds between health-check retries


class VercelClient:
    """Wrapper around the Vercel REST API (v13)."""

    def __init__(self) -> None:
        self.token = os.environ["VERCEL_TOKEN"]
        self.project_id = os.environ["VERCEL_PROJECT_ID"]
        self.org_id = os.environ.get("VERCEL_ORG_ID", "")
        self.project_name = os.environ["VERCEL_PROJECT_NAME"]
        self.github_repo = os.environ["GITHUB_REPO"]

        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        logger.debug(
            "VercelClient initialised — project=%s id=%s",
            self.project_name,
            self.project_id,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _team_params(self) -> dict:
        """Return a query-param dict with teamId if VERCEL_ORG_ID is set."""
        if self.org_id:
            return {"teamId": self.org_id}
        return {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def deploy_branch(self, branch: str, issue_key: str) -> dict:
        """Trigger a Vercel preview deployment from a GitHub branch.

        Args:
            branch:    Git branch name, e.g. ``feature/ZHTP-1-add-todo-app``.
            issue_key: Jira issue key used for logging context.

        Returns:
            The raw deployment object returned by the Vercel API, which
            includes at minimum ``id``, ``url``, and ``readyState``.
        """
        url = f"{_BASE_URL}/v13/deployments"
        params = self._team_params()

        payload = {
            "name": self.project_name,
            "gitSource": {
                "type": "github",
                "repo": self.github_repo,
                "ref": branch,
            },
            "projectId": self.project_id,
            "target": "preview",
        }

        logger.info(
            "Triggering Vercel deployment — issue=%s branch=%s project=%s",
            issue_key,
            branch,
            self.project_name,
        )

        response = requests.post(
            url, json=payload, headers=self.headers, params=params
        )
        response.raise_for_status()

        deployment = response.json()
        logger.info(
            "Deployment created — id=%s readyState=%s url=%s",
            deployment.get("id"),
            deployment.get("readyState"),
            deployment.get("url"),
        )
        return deployment

    def wait_for_ready(self, deployment_id: str, timeout: int = 300) -> str:
        """Poll until the deployment reaches READY state.

        Args:
            deployment_id: The Vercel deployment ID (e.g. ``dpl_abc123``).
            timeout:       Maximum seconds to wait before raising TimeoutError.

        Returns:
            The fully-qualified deployment URL (``https://…vercel.app``).

        Raises:
            TimeoutError:  Raised when ``timeout`` seconds elapse without
                           the deployment becoming ready.
            RuntimeError:  Raised when the deployment enters an ERROR or
                           CANCELED state.
        """
        url = f"{_BASE_URL}/v13/deployments/{deployment_id}"
        params = self._team_params()
        elapsed = 0

        logger.info(
            "Waiting for deployment %s to become READY (timeout=%ds)…",
            deployment_id,
            timeout,
        )

        while elapsed < timeout:
            response = requests.get(url, headers=self.headers, params=params)
            response.raise_for_status()

            data = response.json()
            ready_state = data.get("readyState", "UNKNOWN")
            dep_url = data.get("url", "")

            logger.debug(
                "Deployment %s — readyState=%s elapsed=%ds",
                deployment_id,
                ready_state,
                elapsed,
            )

            if ready_state == "READY":
                full_url = f"https://{dep_url}" if not dep_url.startswith("http") else dep_url
                logger.info(
                    "Deployment %s is READY at %s (elapsed=%ds)",
                    deployment_id,
                    full_url,
                    elapsed,
                )
                return full_url

            if ready_state in ("ERROR", "CANCELED"):
                raise RuntimeError(
                    f"Deployment {deployment_id} entered state '{ready_state}' "
                    f"after {elapsed}s."
                )

            time.sleep(_POLL_INTERVAL)
            elapsed += _POLL_INTERVAL

        raise TimeoutError(
            f"Deployment {deployment_id} did not become READY within {timeout}s. "
            f"Last known state: {ready_state}"
        )

    def health_check(self, url: str) -> bool:
        """Verify that the deployed URL responds with HTTP 200.

        Retries up to ``_HEALTH_CHECK_RETRIES`` times with a short delay.

        Args:
            url: The deployment URL to check.

        Returns:
            ``True`` if a 200 response is received, ``False`` otherwise.
        """
        logger.info("Health-checking %s (max %d attempts)…", url, _HEALTH_CHECK_RETRIES)

        for attempt in range(1, _HEALTH_CHECK_RETRIES + 1):
            try:
                response = requests.get(url, timeout=15)
                if response.status_code == 200:
                    logger.info(
                        "Health check passed — %s returned HTTP 200 (attempt %d)",
                        url,
                        attempt,
                    )
                    return True

                logger.warning(
                    "Health check attempt %d/%d — HTTP %d from %s",
                    attempt,
                    _HEALTH_CHECK_RETRIES,
                    response.status_code,
                    url,
                )
            except requests.RequestException as exc:
                logger.warning(
                    "Health check attempt %d/%d — request error: %s",
                    attempt,
                    _HEALTH_CHECK_RETRIES,
                    exc,
                )

            if attempt < _HEALTH_CHECK_RETRIES:
                time.sleep(_HEALTH_CHECK_DELAY)

        logger.error("Health check FAILED for %s after %d attempts.", url, _HEALTH_CHECK_RETRIES)
        return False
