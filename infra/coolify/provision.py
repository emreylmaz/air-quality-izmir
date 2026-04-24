"""Desired-state reconciler.

Reads ``infra/coolify/config.yaml``, compares against Coolify, applies diff.

Usage:
    python -m infra.coolify.provision plan        # dry-run, show diff
    python -m infra.coolify.provision apply       # execute with confirmation
    python -m infra.coolify.provision status      # current state summary
    python -m infra.coolify.provision destroy     # dev only, double-confirm

The reconciler is stateless (no Terraform-like local state file). Coolify is
the single source of truth; desired state lives in ``config.yaml``.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from infra.coolify.client import CoolifyClient, CoolifyError

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path("infra/coolify/config.yaml")


# ---------------------------------------------------------------------------
# Config model
# ---------------------------------------------------------------------------


def load_config(path: Path) -> dict[str, Any]:
    """Load and return the desired-state YAML."""
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


@dataclass
class Action:
    """A single reconciliation step."""

    op: str  # create | update | update_env | noop
    kind: str  # project | database | application | service
    name: str
    details: dict[str, Any]


def plan(client: CoolifyClient, config: dict[str, Any]) -> list[Action]:
    """Compute the diff between desired (config) and actual (Coolify) state."""
    actions: list[Action] = []

    # ---- Project ----
    project_name = config["project"]["name"]
    existing_projects = client.list_projects()
    if not any(p.get("name") == project_name for p in existing_projects):
        actions.append(Action(op="create", kind="project", name=project_name, details={}))

    # ---- Databases ----
    existing_dbs = client.list_databases()
    db_names = {d.get("name") for d in existing_dbs}
    for db in config.get("databases", []):
        if db["name"] not in db_names:
            actions.append(Action(op="create", kind="database", name=db["name"], details=db))

    # ---- Applications ----
    existing_apps = client.list_applications()
    apps_by_name = {a.get("name"): a for a in existing_apps}
    for app in config.get("applications", []):
        current = apps_by_name.get(app["name"])
        if not current:
            actions.append(Action(op="create", kind="application", name=app["name"], details=app))
            continue
        # Env diff
        desired_envs = {item["key"] for item in app.get("env", [])}
        try:
            current_envs = {item["key"] for item in client.get_app_envs(current["uuid"])}
        except CoolifyError:
            current_envs = set()
        missing = desired_envs - current_envs
        if missing:
            actions.append(
                Action(
                    op="update_env",
                    kind="application",
                    name=app["name"],
                    details={"missing": sorted(missing), "app": app},
                )
            )

    # ---- Services ----
    existing_services = client.list_services()
    service_names = {s.get("name") for s in existing_services}
    for svc in config.get("services", []):
        if svc["name"] not in service_names:
            actions.append(Action(op="create", kind="service", name=svc["name"], details=svc))

    return actions


def print_plan(actions: list[Action]) -> None:
    """Render plan as a human-readable table."""
    if not actions:
        print("[OK] No changes needed. Current state matches desired state.")
        return
    symbols = {
        "create": "+",
        "update": "~",
        "update_env": "~",
        "delete": "-",
        "noop": "=",
    }
    print(f"\n{len(actions)} change(s) planned:\n")
    for action in actions:
        symbol = symbols.get(action.op, "?")
        line = f"  {symbol} {action.kind}: {action.name}"
        if action.op == "update_env":
            missing = action.details.get("missing", [])
            line += f"  (missing envs: {', '.join(missing)})"
        print(line)


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def _resolve_server_uuid(
    client: CoolifyClient, config: dict[str, Any], cache: dict[str, str]
) -> str:
    name = config["server"]["name"]
    if name not in cache:
        cache[name] = client.get_server_uuid(name)
    return cache[name]


def _resolve_project_uuid(
    client: CoolifyClient, config: dict[str, Any], cache: dict[str, str]
) -> str:
    name = config["project"]["name"]
    if name not in cache:
        proj = next(p for p in client.list_projects() if p.get("name") == name)
        cache[name] = str(proj["uuid"])
    return cache[name]


def apply_actions(client: CoolifyClient, config: dict[str, Any], actions: list[Action]) -> None:
    """Execute planned actions sequentially. Idempotent at the ensure_* level."""
    uuid_cache: dict[str, str] = {}

    for action in actions:
        logger.info("[apply] %s %s %s", action.op, action.kind, action.name)

        if action.kind == "project" and action.op == "create":
            client.ensure_project(action.name, config["project"].get("description", ""))
            continue

        if action.kind == "database" and action.op == "create":
            db = action.details
            server_uuid = _resolve_server_uuid(client, config, uuid_cache)
            project_uuid = _resolve_project_uuid(client, config, uuid_cache)
            client.ensure_postgresql(
                project_uuid=project_uuid,
                environment_name=db.get("environment", "production"),
                server_uuid=server_uuid,
                name=db["name"],
                image=db.get("image", "postgres:16.4-alpine"),
                is_public=db.get("is_public", False),
            )
            continue

        if action.kind == "application" and action.op == "create":
            app = action.details
            server_uuid = _resolve_server_uuid(client, config, uuid_cache)
            project_uuid = _resolve_project_uuid(client, config, uuid_cache)
            created = client.ensure_public_app(
                project_uuid=project_uuid,
                environment_name=app.get("environment", "production"),
                server_uuid=server_uuid,
                name=app["name"],
                git_repository=app["git_repository"],
                git_branch=app.get("git_branch", "main"),
                build_pack=app.get("build_pack", "dockerfile"),
                dockerfile_location=app.get("dockerfile_location"),
                ports_exposes=app.get("ports_exposes"),
            )
            if app.get("env"):
                client.upsert_envs_bulk(created["uuid"], app["env"])
            continue

        if action.kind == "application" and action.op == "update_env":
            app = action.details["app"]
            current = next(a for a in client.list_applications() if a.get("name") == action.name)
            client.upsert_envs_bulk(current["uuid"], app["env"])
            continue

        if action.kind == "service" and action.op == "create":
            svc = action.details
            server_uuid = _resolve_server_uuid(client, config, uuid_cache)
            project_uuid = _resolve_project_uuid(client, config, uuid_cache)
            client.ensure_service(
                project_uuid=project_uuid,
                environment_name=svc.get("environment", "production"),
                server_uuid=server_uuid,
                name=svc["name"],
                service_type=svc["type"],
            )
            continue

        logger.warning("Unhandled action: %s %s %s", action.op, action.kind, action.name)


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def print_status(client: CoolifyClient, config: dict[str, Any]) -> None:
    """Render current state of all configured resources."""
    print("\n=== Coolify Status ===\n")

    projects = {p.get("name"): p for p in client.list_projects()}
    dbs = {d.get("name"): d for d in client.list_databases()}
    apps = {a.get("name"): a for a in client.list_applications()}
    services = {s.get("name"): s for s in client.list_services()}

    project_name = config["project"]["name"]
    print(f"Project: {project_name}  ->  {'OK' if project_name in projects else 'MISSING'}")

    print("\nDatabases:")
    for db in config.get("databases", []):
        status = dbs.get(db["name"], {}).get("status", "MISSING")
        print(f"  - {db['name']}: {status}")

    print("\nApplications:")
    for app in config.get("applications", []):
        status = apps.get(app["name"], {}).get("status", "MISSING")
        print(f"  - {app['name']}: {status}")

    print("\nServices:")
    for svc in config.get("services", []):
        status = services.get(svc["name"], {}).get("status", "MISSING")
        print(f"  - {svc['name']}: {status}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Coolify desired-state reconciler.")
    parser.add_argument("command", choices=["plan", "apply", "status", "destroy"])
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip interactive confirmation on apply.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    config = load_config(Path(args.config))

    with CoolifyClient() as client:
        if args.command == "plan":
            actions = plan(client, config)
            print_plan(actions)
            return 0

        if args.command == "apply":
            actions = plan(client, config)
            print_plan(actions)
            if not actions:
                return 0
            if not args.yes:
                confirm = input("\nDevam edilsin mi? [y/N]: ").strip().lower()
                if confirm != "y":
                    print("Aborted.")
                    return 1
            apply_actions(client, config, actions)
            print("\n[OK] Applied successfully.")
            return 0

        if args.command == "status":
            print_status(client, config)
            return 0

        if args.command == "destroy":
            print(
                "destroy is not implemented — use Coolify UI for resource deletion "
                "to avoid accidental data loss."
            )
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
