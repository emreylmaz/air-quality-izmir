"""Coolify v4 REST API client — idempotent, secret-safe.

Conventions:
- Every method logs METHOD PATH STATUS (no auth header, no body with secrets)
- 429 → retry respecting Retry-After header
- 5xx → retry 3x with exponential backoff
- 4xx → raise CoolifyError with parsed body

TODO: endpoint response shapes should be recorded in docs/coolify-api-notes.md
on first real call — see docs/ASSUMPTIONS.md.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)


@dataclass
class CoolifyConfig:
    """Coolify connection configuration.

    Loaded from ``~/.config/air-quality/coolify.env`` (gitignored path) or
    directly from environment variables ``COOLIFY_BASE_URL`` and
    ``COOLIFY_API_TOKEN``.
    """

    base_url: str
    token: str
    timeout: float = 30.0

    @classmethod
    def from_env(cls, config_path: Path | None = None) -> CoolifyConfig:
        """Load config from user-level env file, falling back to process env."""
        if config_path is None:
            config_path = Path.home() / ".config" / "air-quality" / "coolify.env"
        if config_path.exists():
            for line in config_path.read_text().splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                key, _, value = stripped.partition("=")
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

        try:
            return cls(
                base_url=os.environ["COOLIFY_BASE_URL"].rstrip("/"),
                token=os.environ["COOLIFY_API_TOKEN"],
            )
        except KeyError as exc:
            raise RuntimeError(
                f"Missing {exc.args[0]}. Set in environment or {config_path}."
            ) from exc


class CoolifyError(Exception):
    """Generic Coolify API error (4xx/5xx)."""


class CoolifyClient:
    """Thin, idempotent wrapper over Coolify v1 REST API.

    Each ``ensure_*`` method is idempotent: it lists existing resources,
    matches by name, and only POSTs when absent. Name is the identity key;
    UUIDs are looked up on demand.
    """

    def __init__(self, config: CoolifyConfig | None = None) -> None:
        self.config = config or CoolifyConfig.from_env()
        self._client = httpx.Client(
            base_url=f"{self.config.base_url}/api/v1",
            headers={
                "Authorization": f"Bearer {self.config.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=self.config.timeout,
        )

    def __repr__(self) -> str:
        # Never leak token.
        return f"CoolifyClient(base_url={self.config.base_url!r}, token=***)"

    def __enter__(self) -> CoolifyClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # ------------------------------------------------------------------
    # Low-level request
    # ------------------------------------------------------------------

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(httpx.HTTPError),
        reraise=True,
    )
    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._client.request(method, path, **kwargs)
        logger.info("coolify %s %s -> %s", method, path, response.status_code)

        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", "60"))
            raise httpx.HTTPError(f"Rate limited, retry after {retry_after}s")
        if response.status_code >= 400:
            raise CoolifyError(
                f"{method} {path} -> {response.status_code}: {response.text}"
            )
        if not response.content:
            return {}
        return response.json()

    # ------------------------------------------------------------------
    # Projects
    # ------------------------------------------------------------------

    def list_projects(self) -> list[dict]:
        data = self._request("GET", "/projects")
        return data.get("data", data) if isinstance(data, dict) else data

    def ensure_project(self, name: str, description: str = "") -> dict:
        existing = next((p for p in self.list_projects() if p.get("name") == name), None)
        if existing:
            logger.info("project %s already exists (uuid=%s)", name, existing.get("uuid"))
            return existing
        return self._request(
            "POST",
            "/projects",
            json={"name": name, "description": description},
        )

    # ------------------------------------------------------------------
    # Servers
    # ------------------------------------------------------------------

    def list_servers(self) -> list[dict]:
        data = self._request("GET", "/servers")
        return data.get("data", data) if isinstance(data, dict) else data

    def get_server_uuid(self, name: str) -> str:
        server = next((s for s in self.list_servers() if s.get("name") == name), None)
        if not server:
            raise CoolifyError(f"Server '{name}' not found in Coolify.")
        return str(server["uuid"])

    # ------------------------------------------------------------------
    # Databases
    # ------------------------------------------------------------------

    def list_databases(self) -> list[dict]:
        data = self._request("GET", "/databases")
        return data.get("data", data) if isinstance(data, dict) else data

    def start_database(self, db_uuid: str) -> dict:
        """Start a stopped database. Coolify uses GET (verified 2026-04-23)."""
        return self._request("GET", f"/databases/{db_uuid}/start")

    def ensure_postgresql(
        self,
        *,
        project_uuid: str,
        environment_name: str,
        server_uuid: str,
        name: str,
        image: str = "postgres:16.4-alpine",
        is_public: bool = False,
        **extra: Any,
    ) -> dict:
        existing = next(
            (d for d in self.list_databases() if d.get("name") == name),
            None,
        )
        if existing:
            logger.info("postgres %s exists (uuid=%s)", name, existing.get("uuid"))
            return existing
        payload = {
            "project_uuid": project_uuid,
            "environment_name": environment_name,
            "server_uuid": server_uuid,
            "name": name,
            "image": image,
            "is_public": is_public,
            **{k: v for k, v in extra.items() if v is not None},
        }
        return self._request("POST", "/databases/postgresql", json=payload)

    # ------------------------------------------------------------------
    # Applications
    # ------------------------------------------------------------------

    def list_applications(self) -> list[dict]:
        data = self._request("GET", "/applications")
        return data.get("data", data) if isinstance(data, dict) else data

    def ensure_public_app(
        self,
        *,
        project_uuid: str,
        environment_name: str,
        server_uuid: str,
        name: str,
        git_repository: str,
        git_branch: str = "main",
        build_pack: str = "dockerfile",
        dockerfile_location: str | None = None,
        ports_exposes: str | None = None,
        **extra: Any,
    ) -> dict:
        existing = next(
            (a for a in self.list_applications() if a.get("name") == name),
            None,
        )
        if existing:
            logger.info("app %s exists (uuid=%s)", name, existing.get("uuid"))
            return existing
        payload = {
            "project_uuid": project_uuid,
            "environment_name": environment_name,
            "server_uuid": server_uuid,
            "name": name,
            "git_repository": git_repository,
            "git_branch": git_branch,
            "build_pack": build_pack,
            "dockerfile_location": dockerfile_location,
            "ports_exposes": ports_exposes,
            **{k: v for k, v in extra.items() if v is not None},
        }
        return self._request("POST", "/applications/public", json=payload)

    def get_app_envs(self, app_uuid: str) -> list[dict]:
        data = self._request("GET", f"/applications/{app_uuid}/envs")
        return data.get("data", data) if isinstance(data, dict) else data

    def upsert_envs_bulk(self, app_uuid: str, variables: list[dict]) -> dict:
        """Bulk upsert env variables.

        Expected shape of ``variables`` items:
            {"key": "FOO", "value": "bar", "is_build_time": False, "is_literal": True}

        Secret values are masked in log output.
        """
        masked = [{**item, "value": "***"} for item in variables]
        logger.info("bulk upsert env for %s: %s", app_uuid, masked)
        return self._request(
            "PATCH",
            f"/applications/{app_uuid}/envs/bulk",
            json={"data": variables},
        )

    def deploy_application(self, app_uuid: str) -> dict:
        return self._request("POST", f"/applications/{app_uuid}/start")

    def restart_application(self, app_uuid: str) -> dict:
        return self._request("POST", f"/applications/{app_uuid}/restart")

    def stop_application(self, app_uuid: str) -> dict:
        return self._request("POST", f"/applications/{app_uuid}/stop")

    # ------------------------------------------------------------------
    # Services (one-click)
    # ------------------------------------------------------------------

    def list_services(self) -> list[dict]:
        data = self._request("GET", "/services")
        return data.get("data", data) if isinstance(data, dict) else data

    def start_service(self, service_uuid: str) -> dict:
        """Start a stopped one-click service. GET verb (verified 2026-04-23)."""
        return self._request("GET", f"/services/{service_uuid}/start")

    def ensure_service(
        self,
        *,
        project_uuid: str,
        environment_name: str,
        server_uuid: str,
        name: str,
        service_type: str,
        **extra: Any,
    ) -> dict:
        """Create a one-click service if absent.

        TODO: doğrula Coolify API docs'ta — service_type değeri Coolify UI'da
        "Add Resource → Service → Search" üzerinden teyit edilmeli.
        ``grafana-with-postgresql`` gibi ID'ler sürüme göre değişebilir.
        """
        existing = next(
            (s for s in self.list_services() if s.get("name") == name),
            None,
        )
        if existing:
            logger.info("service %s exists (uuid=%s)", name, existing.get("uuid"))
            return existing
        payload = {
            "project_uuid": project_uuid,
            "environment_name": environment_name,
            "server_uuid": server_uuid,
            "name": name,
            "type": service_type,
            **{k: v for k, v in extra.items() if v is not None},
        }
        return self._request("POST", "/services", json=payload)
