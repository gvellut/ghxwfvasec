import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import click
import yaml

# Constant for the "Actions" (repo-level) environment filter
ACTIONS_ENV = "actions"

# --- SCANNER LOGIC ---


class WorkflowScanner:
    def __init__(self):
        # Regex to catch: secrets.NAME, vars.NAME
        self.pattern = re.compile(r"\b(secrets|vars)\.([a-zA-Z0-9_-]+)\b")
        self.results = {}
        self.global_usage = {"secrets": set(), "vars": set()}

    def _extract_from_string(self, text: str, file_refs: Dict[str, set]) -> None:
        matches = self.pattern.findall(text)
        for context, name in matches:
            self.global_usage[context].add(name)
            file_refs[context].add(name)

    def _walk_node(self, node: Any, file_refs: Dict[str, set]) -> None:
        if isinstance(node, dict):
            for value in node.values():
                self._walk_node(value, file_refs)
        elif isinstance(node, list):
            for item in node:
                self._walk_node(item, file_refs)
        elif isinstance(node, str):
            self._extract_from_string(node, file_refs)

    def scan_file(self, file_path: Path) -> None:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)

            if not data:
                return

            file_refs = {"secrets": set(), "vars": set()}
            self._walk_node(data, file_refs)

            if file_refs["secrets"] or file_refs["vars"]:
                self.results[file_path.name] = file_refs

        except Exception:
            pass

    def scan_path(self, path: Path) -> None:
        files = []
        if path.is_file():
            files = [path]
        elif path.is_dir():
            files = list(path.glob("**/*.yml")) + list(path.glob("**/*.yaml"))

        with click.progressbar(files, label="Scanning Workflow Files") as bar:
            for f in bar:
                self.scan_file(f)


# --- GITHUB CLI INTEGRATION ---


class GHInspector:
    def __init__(self, repo: Optional[str] = None):
        if not shutil.which("gh"):
            raise click.ClickException(
                "GitHub CLI ('gh') not found. Please install it."
            )

        self.repo_str = repo
        # -R is valid for 'gh secret ...' commands but NOT 'gh api'
        self.repo_arg = ["-R", repo] if repo else []
        self._check_auth()

    def _check_auth(self):
        try:
            subprocess.run(["gh", "auth", "status"], check=True, capture_output=True)
        except subprocess.CalledProcessError:
            raise click.ClickException(
                "You are not logged into gh CLI. Run 'gh auth login'."
            )

    def _run_json(self, cmd: List[str]) -> Any:
        # Helper for standard gh commands (secret list, etc) which support -R
        full_cmd = ["gh"] + cmd + self.repo_arg + ["--json", "name"]
        try:
            res = subprocess.run(full_cmd, capture_output=True, text=True, check=True)
            return json.loads(res.stdout)
        except subprocess.CalledProcessError:
            return []

    def fetch_data(self):
        click.echo("🔄 Fetching definitions from GitHub...")

        # 1. Fetch Repo Level
        repo_secrets = set(x["name"] for x in self._run_json(["secret", "list"]))
        repo_vars = set(x["name"] for x in self._run_json(["variable", "list"]))

        # 2. Fetch Environment List
        # FIX: Construct the endpoint manually to avoid passing -R to gh api
        if self.repo_str:
            endpoint = f"repos/{self.repo_str}/environments"
        else:
            # Use gh magic placeholders for current dir context
            endpoint = "repos/:owner/:repo/environments"

        api_cmd = ["gh", "api", endpoint, "--jq", ".environments[].name"]

        envs = {}
        try:
            res = subprocess.run(api_cmd, capture_output=True, text=True, check=True)
            env_names = res.stdout.strip().split("\n") if res.stdout.strip() else []
        except subprocess.CalledProcessError as e:
            click.secho(
                f"Warning: Failed to fetch environments via API: {e.stderr}",
                fg="yellow",
            )
            env_names = []

        # 3. Fetch Details per Environment
        if env_names:
            with click.progressbar(env_names, label="Fetching Envs") as bar:
                for env in bar:
                    if not env:
                        continue
                    # Note: "secret list" DOES support self.repo_arg
                    e_secrets = set(
                        x["name"]
                        for x in self._run_json(["secret", "list", "--env", env])
                    )
                    e_vars = set(
                        x["name"]
                        for x in self._run_json(["variable", "list", "--env", env])
                    )
                    envs[env] = {"secrets": e_secrets, "vars": e_vars}

        return {"repo_secrets": repo_secrets, "repo_vars": repo_vars, "envs": envs}


# --- REPORTING ---


def generate_output(scanner, gh_data, filter_envs: Optional[Tuple[str, ...]] = None):
    """
    Generate the audit report.

    Args:
        scanner: The workflow scanner with results
        gh_data: GitHub data (repo/env secrets and vars)
        filter_envs: Optional tuple of environment names to filter by.
                     Use ACTIONS_ENV constant for repo-level.
    """
    lines = []

    def h1(text):
        lines.append(f"\n{'=' * 60}\n {text}\n{'=' * 60}")

    def h2(text):
        lines.append(f"\n--- {text} ---")

    def line(text):
        lines.append(text)

    h1("GITHUB WORKFLOW CONFIG AUDIT")

    req_vars = sorted(scanner.global_usage["vars"])
    req_secrets = sorted(scanner.global_usage["secrets"])

    line(f"Total Unique Vars Found:    {len(req_vars)}")
    line(f"Total Unique Secrets Found: {len(req_secrets)}")

    available_envs = (
        sorted(gh_data["envs"].keys()) if gh_data and "envs" in gh_data else []
    )

    # Determine which environments to show
    show_actions = filter_envs is None or ACTIONS_ENV in filter_envs
    if filter_envs is None:
        envs_to_show = available_envs
    else:
        envs_to_show = [e for e in available_envs if e in filter_envs]

    def analyze_item(name, kind, repo_set, env_map):
        if not gh_data:
            return None

        status_map = {}

        # 1. Check Repo Level
        is_in_repo = name in repo_set

        # Special case: GITHUB_TOKEN is auto-generated
        if name == "GITHUB_TOKEN" and kind == "secrets":
            status_map["Repo"] = " [🤖 Auto]"
            for env in available_envs:
                status_map[env] = " [🤖 Auto]"
            return status_map

        status_map["Repo"] = " [✅ Repo]" if is_in_repo else " [❌ MISSING]"

        # 2. Check Env Level
        for env_name in available_envs:
            env_data = env_map.get(env_name, {}).get(kind, set())
            is_in_env = name in env_data

            if is_in_env:
                status_map[env_name] = " [✅ Env]"
            elif is_in_repo:
                # GitHub Actions inherits repo secrets if not overridden
                status_map[env_name] = " [✅ Inherited]"
            else:
                status_map[env_name] = " [❌ MISSING]"

        return status_map

    def print_section(title, items, kind, repo_set):
        if not items:
            return
        h2(title)

        env_map = gh_data.get("envs", {}) if gh_data else {}

        for item in items:
            line(f"  • {item}")
            status_map = analyze_item(item, kind, repo_set, env_map)

            if status_map:
                if show_actions:
                    line(f"    |__ {'(Actions)':<15} {status_map['Repo']}")
                if envs_to_show:
                    for env in envs_to_show:
                        line(f"    |__ {env:<15} {status_map[env]}")
            else:
                line("    (No GitHub data fetched)")

    print_section(
        "VARIABLES (vars.*)",
        req_vars,
        "vars",
        gh_data.get("repo_vars", set()) if gh_data else set(),
    )

    print_section(
        "SECRETS (secrets.*)",
        req_secrets,
        "secrets",
        gh_data.get("repo_secrets", set()) if gh_data else set(),
    )

    h2("USAGE BY FILE")
    for fname, refs in scanner.results.items():
        line(f"📄 {fname}")
        if refs["vars"]:
            line(f"   vars    : {', '.join(sorted(refs['vars']))}")
        if refs["secrets"]:
            line(f"   secrets : {', '.join(sorted(refs['secrets']))}")

    return "\n".join(lines)


# --- CLI ENTRY POINT ---


def get_repo_from_git():
    """Try to extract repo (owner/repo) from current git directory using gh CLI."""
    # Check if we're in a git directory
    git_check = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        capture_output=True,
        text=True,
    )
    if git_check.returncode != 0:
        return None

    # Get repo name using gh
    try:
        result = subprocess.run(
            ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
            capture_output=True,
            text=True,
            check=True,
        )
        repo_name = result.stdout.strip()
        return repo_name if repo_name else None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def validate_path_callback(ctx, param, value):
    """Callback to validate and set default path for workflows folder."""
    if value is None:
        # Use default .github/workflows
        default_path = Path(".github/workflows")
        click.echo(f"ℹ️  Using default workflow path: {default_path}")
        if not default_path.exists():
            raise click.BadParameter(
                f"Default workflow directory '{default_path}' does not exist. "
                "Please specify a path explicitly or run from a repository root."
            )
        return default_path

    # Convert to Path and validate existence
    path = Path(value)
    if not path.exists():
        raise click.BadParameter(f"Path '{value}' does not exist.")
    return path


@click.command()
@click.argument(
    "path",
    type=click.Path(path_type=Path),
    required=False,
    default=None,
    callback=validate_path_callback,
)
@click.option(
    "--auto/--no-auto",
    default=True,
    help="Enable auto-detection of repo and default path (default: enabled).",
)
@click.option(
    "--check-gh/--no-check-gh",
    default=True,
    help="Fetch definitions from GitHub (default: enabled).",
)
@click.option("--repo", default=None, help="Target GitHub repo (owner/repo).")
@click.option("--out", type=click.Path(), help="Write summary to file.")
@click.option(
    "--env",
    "-e",
    "envs",
    multiple=True,
    help=f"Filter output by environment. Use '{ACTIONS_ENV}' for repo-level, or an environment name. Can be specified multiple times.",
)
def main(path, auto, check_gh, repo, out, envs):
    """
    Scans GitHub Workflow YAML files and audits against GitHub Repo/Env definitions.
    """
    # Auto-detect repo if --auto is enabled and --repo not provided
    if auto and repo is None and check_gh:
        detected_repo = get_repo_from_git()
        if detected_repo:
            click.echo(f"ℹ️  Auto-detected repo: {detected_repo}")
            repo = detected_repo
        else:
            click.secho(
                "⚠️  Could not auto-detect repo. Use --repo to specify manually, "
                "or --no-auto to disable auto-detection.",
                fg="yellow",
            )

    scanner = WorkflowScanner()
    scanner.scan_path(path)

    if not scanner.results:
        click.secho("No variables or secrets found in path.", fg="yellow")
        return

    gh_data = None
    if check_gh:
        try:
            inspector = GHInspector(repo)
            gh_data = inspector.fetch_data()
        except click.ClickException as e:
            click.secho(str(e), fg="red")
            sys.exit(1)

    # Validate requested environments
    filter_envs = envs if envs else None
    if filter_envs and gh_data:
        available_envs = set(gh_data.get("envs", {}).keys())
        valid_envs = available_envs | {ACTIONS_ENV}
        for env in filter_envs:
            if env not in valid_envs:
                click.secho(
                    f"Error: Environment '{env}' does not exist. "
                    f"Available: {ACTIONS_ENV}, {', '.join(sorted(available_envs))}",
                    fg="red",
                )
                sys.exit(1)

    report_text = generate_output(scanner, gh_data, filter_envs)

    # Print with color parsing
    for line in report_text.split("\n"):
        color = None
        if "[❌ MISSING]" in line:
            color = "red"
        elif "[✅ Env]" in line:
            color = "cyan"
        elif "[✅ Inherited]" in line:
            color = "green"
        elif "[✅ Repo]" in line:
            color = "green"
        elif "[🤖 Auto]" in line:
            color = "blue"
        elif line.startswith("="):
            click.secho(line, bold=True)
            continue
        elif line.startswith("---"):
            click.secho(line, bold=True, fg="yellow")
            continue
        elif line.strip().startswith("•"):
            click.secho(line, bold=True)
            continue

        if color:
            click.secho(line, fg=color)
        else:
            click.echo(line)

    if out:
        with open(out, "w", encoding="utf-8") as f:
            f.write(report_text)
        click.secho(f"\n💾 Report saved to: {out}", fg="blue")


if __name__ == "__main__":
    main()
