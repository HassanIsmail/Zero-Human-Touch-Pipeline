"""
pipeline/jira_client.py

Jira REST API v3 client for the Zero Human Touch Pipeline.

Reads configuration from environment variables:
    JIRA_URL           — e.g. https://yourorg.atlassian.net
    JIRA_EMAIL         — Atlassian account email
    JIRA_API_TOKEN     — API token generated at id.atlassian.com
    JIRA_PROJECT_KEY   — e.g. ZHTP
"""

import os
from typing import List, Optional

import requests
from requests.auth import HTTPBasicAuth

from pipeline.logger import get_logger

logger = get_logger(__name__)


class JiraClient:
    """Thin wrapper around the Jira REST API v3."""

    def __init__(self) -> None:
        self.base_url = os.environ["JIRA_URL"].rstrip("/")
        self.email = os.environ["JIRA_EMAIL"]
        self.api_token = os.environ["JIRA_API_TOKEN"]
        self.project_key = os.environ["JIRA_PROJECT_KEY"]

        self.auth = HTTPBasicAuth(self.email, self.api_token)
        self.headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        logger.debug(
            "JiraClient initialised — project=%s url=%s",
            self.project_key,
            self.base_url,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def get_new_stories(self) -> List[dict]:
        jql = (
            f'project = "{self.project_key}" '
            'AND labels = "ai-ready" '
            'AND status = "To Do"'
        )

        url = f"{self.base_url}/rest/api/3/search/jql"

        payload = {
            "jql": jql,
            "fields": ["summary", "attachment", "labels", "status"],
            "maxResults": 50
        }

        logger.info("Querying Jira for new stories — JQL: %s", jql)

        response = requests.post(
            url,
            json=payload,
            headers=self.headers,
            auth=self.auth
        )

        response.raise_for_status()

        issues = response.json().get("issues", [])
        logger.info("Found %d new story/stories", len(issues))
        return issues

    def download_requirements(self, issue_key: str) -> Optional[str]:
        """Download the attachment named exactly ``requirements.md`` from an issue.

        Args:
            issue_key: The Jira issue key, e.g. ``ZHTP-42``.

        Returns:
            The text content of the attachment, or ``None`` if no matching
            attachment exists.
        """
        logger.info("Fetching attachments for %s", issue_key)
        url = f"{self.base_url}/rest/api/3/issue/{issue_key}"
        params = {"fields": "attachment"}

        response = requests.get(url, headers=self.headers, auth=self.auth, params=params)
        response.raise_for_status()

        attachments = response.json().get("fields", {}).get("attachment", [])
        logger.debug("%s has %d attachment(s)", issue_key, len(attachments))

        for attachment in attachments:
            if attachment.get("filename") == "requirements.md":
                content_url = attachment["content"]
                logger.info(
                    "Downloading requirements.md from %s (id=%s)",
                    issue_key,
                    attachment.get("id"),
                )
                dl_response = requests.get(
                    content_url,
                    auth=self.auth,
                    # Do NOT include the JSON Accept header — we want raw bytes.
                    headers={"Accept": "*/*"},
                )
                dl_response.raise_for_status()
                return dl_response.text

        logger.warning("No requirements.md attachment found on %s", issue_key)
        return None

    def get_transitions(self, issue_key: str) -> List[dict]:
        """Return the available workflow transitions for an issue.

        Args:
            issue_key: The Jira issue key.

        Returns:
            The raw list of transition dicts from the Jira API.
        """
        url = f"{self.base_url}/rest/api/3/issue/{issue_key}/transitions"
        logger.debug("Fetching transitions for %s", issue_key)

        response = requests.get(url, headers=self.headers, auth=self.auth)
        response.raise_for_status()

        transitions = response.json().get("transitions", [])
        logger.debug(
            "Available transitions for %s: %s",
            issue_key,
            [f"{t['id']}:{t['name']}" for t in transitions],
        )
        return transitions

    def transition_issue(self, issue_key: str, target_status: str) -> bool:
        """Move an issue to a new workflow status.

        The method searches for a transition whose ``name`` or ``to.name``
        contains ``target_status`` (case-insensitive) and executes the first
        match.

        Args:
            issue_key:     The Jira issue key.
            target_status: Substring to match against transition names, e.g.
                           ``"In Progress"``.

        Returns:
            ``True`` if the transition was applied successfully, ``False`` if
            no matching transition was found or the request failed.
        """
        transitions = self.get_transitions(issue_key)
        target_lower = target_status.lower()

        matched = None
        for t in transitions:
            t_name = t.get("name", "").lower()
            to_name = t.get("to", {}).get("name", "").lower()
            if target_lower in t_name or target_lower in to_name:
                matched = t
                break

        if matched is None:
            logger.warning(
                "No transition matching '%s' found for %s. "
                "Available transitions: %s",
                target_status,
                issue_key,
                [(t["id"], t["name"]) for t in transitions],
            )
            return False

        url = f"{self.base_url}/rest/api/3/issue/{issue_key}/transitions"
        payload = {"transition": {"id": matched["id"]}}
        logger.info(
            "Transitioning %s to '%s' via transition id=%s name='%s'",
            issue_key,
            target_status,
            matched["id"],
            matched["name"],
        )

        response = requests.post(
            url, json=payload, headers=self.headers, auth=self.auth
        )
        if response.status_code in (200, 204):
            logger.info("Successfully transitioned %s → %s", issue_key, matched["name"])
            return True

        logger.error(
            "Failed to transition %s: HTTP %s — %s",
            issue_key,
            response.status_code,
            response.text,
        )
        return False

    def add_comment(self, issue_key: str, text: str) -> None:
        """Post a comment to a Jira issue using Atlassian Document Format (ADF).

        Args:
            issue_key: The Jira issue key.
            text:      Plain text content for the comment body.
        """
        url = f"{self.base_url}/rest/api/3/issue/{issue_key}/comment"
        payload = {
            "body": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [
                            {
                                "type": "text",
                                "text": text,
                            }
                        ],
                    }
                ],
            }
        }
        logger.info("Adding comment to %s", issue_key)
        logger.debug("Comment text (first 200 chars): %.200s", text)

        response = requests.post(
            url, json=payload, headers=self.headers, auth=self.auth
        )
        if response.status_code in (200, 201):
            logger.info("Comment added to %s", issue_key)
        else:
            logger.error(
                "Failed to add comment to %s: HTTP %s — %s",
                issue_key,
                response.status_code,
                response.text,
            )
