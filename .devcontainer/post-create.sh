#!/usr/bin/env bash
# Codespaces post-create bootstrap.
# Idempotent: safe to re-run via `Rebuild Container`.
set -euo pipefail

REPO_DIR="${PWD}"
echo "[post-create] repo: ${REPO_DIR}"

# 1. Claude Code CLI (native installer, ~/.local/bin)
if ! command -v claude >/dev/null 2>&1; then
  echo "[post-create] installing Claude Code CLI"
  curl -fsSL https://claude.ai/install.sh | bash
fi

# 2. Python deps — dev + ingestion only.
#    `processing` (PySpark) skipped per TD-05; spark stack runs in Docker.
echo "[post-create] installing Python deps (dev + ingestion)"
python -m pip install --upgrade pip
pip install -e ".[dev,ingestion]"

# 3. Pre-commit hooks
if [ -f ".pre-commit-config.yaml" ]; then
  echo "[post-create] installing pre-commit hooks"
  pre-commit install --install-hooks
fi

# 4. direnv stub — Codespaces does not preload direnv; copy example so user can edit
if [ ! -f ".envrc" ] && [ -f ".envrc.example" ]; then
  echo "[post-create] seeding .envrc from .envrc.example (edit then 'direnv allow')"
  cp .envrc.example .envrc
fi

# 5. Sanity check
echo "[post-create] versions:"
python --version
pip --version
docker --version 2>/dev/null || echo "  docker: not yet ready (DinD warming up)"
claude --version 2>/dev/null || echo "  claude: open new shell to pick up PATH"

echo "[post-create] done. Next steps:"
echo "  - export ANTHROPIC_API_KEY (Codespaces secret) or run 'claude login'"
echo "  - 'make up' to start full stack (needs 4-core/16GB host)"
echo "  - 'make migrate && make seed' to apply schema"
echo "  - 'pytest -m \"not integration\"' for fast unit suite"
