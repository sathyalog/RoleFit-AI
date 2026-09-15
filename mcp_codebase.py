import os
import glob
from typing import List, Dict, Any
from langchain_core.tools import tool

# Default local projects directory
DEFAULT_PROJECTS_DIR = os.path.expanduser("~/Sathya_Drive/DEV Workspace")


def scan_local_projects(query: str, root_dir: str = DEFAULT_PROJECTS_DIR) -> str:
    """Scans ALL local repositories for exact substring matches in source, config, and documentation files."""
    if not os.path.exists(root_dir):
        return f"Projects directory '{root_dir}' not found on local machine."

    clean_query = query.strip()
    if not clean_query:
        return "No valid search terms provided."

    query_lower = clean_query.lower()
    results = []

    # Fetch all project directories (excluding hidden workspace folders)
    project_dirs = [
        d for d in glob.glob(os.path.join(root_dir, "*"))
        if os.path.isdir(d) and not os.path.basename(d).startswith(".")
    ]

    # Expanded file extension coverage including configuration & markups
    supported_exts = [
        "*.py", "*.yaml", "*.yml", "*.json", "*.toml", 
        "*.md", "*.ts", "*.js", "*.swift", "*.ipynb", "*.txt"
    ]

    for project_path in project_dirs:
        project_name = os.path.basename(project_path)

        for ext in supported_exts:
            for file_path in glob.glob(
                os.path.join(project_path, f"**/{ext}"), recursive=True
            ):
                # Ignore dependencies, build outputs, and virtual environments
                if any(
                    ignore_dir in file_path
                    for ignore_dir in [".venv", "venv", "node_modules", ".git", "build", "dist", "__pycache__", ".tox"]
                ):
                    continue

                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        lines = f.readlines()
                        for idx, line in enumerate(lines):
                            # Direct substring match against raw line
                            if query_lower in line.lower():
                                start = max(0, idx - 8)
                                end = min(len(lines), idx + 8)
                                snippet = "".join(lines[start:end])
                                rel_path = os.path.relpath(file_path, project_path)

                                results.append(
                                    f"💻 **MATCH**: `{project_name}` -> `{rel_path}` (Line {idx + 1})\n"
                                    f"```yaml\n{snippet}\n```"
                                )
                                break  # Prevent multiple snippets from the same file
                except Exception:
                    continue

    if not results:
        return f"No matches found across local projects for query: '{clean_query}'"

    return "\n\n".join(results)


@tool
def search_local_codebase_tool(search_query: str) -> str:
    """Searches local project repositories for exact string occurrences."""
    return scan_local_projects(search_query)
