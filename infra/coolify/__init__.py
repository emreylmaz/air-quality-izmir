"""Coolify v4 API integration — IaC-like provisioning.

Usage:
    python -m infra.coolify.provision plan
    python -m infra.coolify.provision apply
    python -m infra.coolify.provision status
    python -m infra.coolify.sync_secrets push
"""

from infra.coolify.client import CoolifyClient, CoolifyConfig, CoolifyError

__all__ = ["CoolifyClient", "CoolifyConfig", "CoolifyError"]
