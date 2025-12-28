# Repository Guidelines

## Project Structure & Module Organization
This repo is a collection of numbered Python demo scripts showcasing OxyGent features. Use files like `00_环境安装.py`, `01_hello_world.py`, `03_RAG.py`, and `04_MoA简单实现.py` as the primary entry points. Shared helpers live in `point_util.py`, while runtime caches belong in `cache_dir/`, sample inputs in `local_file/`, and MCP definitions in `mcp_servers/`. Keep new demos numbered and descriptive (for example, `23_new_feature.py`) and keep reusable logic in a utility module instead of duplicating it across scripts.

## Build, Test, and Development Commands
- Create the Python environment and install dependencies:
  ```bash
  conda create -n oxy_env python==3.10
  conda activate oxy_env
  pip install oxygent
  ```
- Configure `.env` with `DEFAULT_LLM_API_KEY`, `DEFAULT_LLM_BASE_URL`, and `DEFAULT_LLM_MODEL_NAME`.
- Run a demo locally:
  ```bash
  python 01_hello_world.py
  python 03_RAG.py
  ```
- Install Node.js if you plan to use MCP tooling (see `README.md`).

## Coding Style & Naming Conventions
Use 4-space indentation, snake_case for functions/variables, and PascalCase for classes. Keep demo filenames numbered with underscores and align style with existing scripts. Prefer small, readable scripts; move shared logic into `point_util.py` or a new `*_util.py` module. Keep comments brief and consistent with the current Chinese/English mix.

## Testing Guidelines
No automated test framework is configured. Validate changes by running the relevant demo scripts and checking console output or the UI. If you introduce tests, place them under `tests/` and document the test command in `README.md`.

## Commit & Pull Request Guidelines
Git history uses short messages like `add` with no formal convention. Use concise imperative messages that name the demo or feature (for example, `add rag demo`). PRs should describe the scenario, list the exact run command(s), and note any configuration or environment changes. Include screenshots or GIFs when output is visual.
