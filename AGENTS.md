# Repository Guidelines

This document describes the conventions and workflows for contributing to this repository.

## Project Structure & Module Organization

The repository is organized as a standard Python project:

```
src/                    # Source code (application or library package)
tests/                  # Test suite, mirroring src/ layout
notebooks/              # Jupyter notebooks for exploration and prototyping
scripts/                # Utility scripts (data processing, automation)
docs/                   # Project documentation
assets/                 # Static assets (images, config templates)
```

All production code lives under `src/`. Tests mirror the source tree — `tests/test_<module>.py` tests `src/<package>/<module>.py`.

## Build, Test, and Development Commands

Dependencies are managed with `uv`.

| Command | Purpose |
|---|---|
| `uv sync` | Install all dependencies. |
| `uv add <package>` | Add a new dependency. |
| `uv run pytest` | Run the full test suite with coverage. |
| `uv run jupyter lab` | Launch Jupyter Lab for interactive development. |

## Coding Style & Naming Conventions

- **Indentation**: 4 spaces. No tabs.
- **Line length**: 88 characters (Black default).
- **Formatting**: `ruff format`. Run before committing.
- **Linting**: `ruff check`. Fix warnings before pushing.
- **Naming**: packages/modules in `snake_case`, classes in `PascalCase`, functions in `snake_case`, constants in `UPPER_SNAKE_CASE`, private helpers prefixed with `_`.

## Testing Guidelines

- **Framework**: `pytest` with `pytest-cov`.
- **Coverage target**: 80%+ on new code.
- **Naming**: `test_<feature>` or `test_<feature>__<scenario>`.

## Commit & Pull Request Guidelines

- **Commit messages**: Follow [Conventional Commits](https://www.conventionalcommits.org/). Format: `<type>: <description>` (types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`).
- **Pull requests**: Every PR includes a description of what and why, a link to the related issue if applicable, and passing lint + tests.

## Agent-Specific Instructions

When developing with AI coding agents:

- Put production code in `src/`, exploration in `notebooks/`.
- Prefix agent-generated branches with `ai/` (e.g., `ai/add-endpoint`).
- Review all agent-generated code before merging.
- Keep this file updated as project conventions evolve.
