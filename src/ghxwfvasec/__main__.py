import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

import click
import yaml

# --- SCANNER LOGIC ---


class WorkflowScanner:
    def __init__(self):
        # Regex to catch: secrets.NAME, vars.NAME, ${{ secrets.NAME }}
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

            # Only store if something was found
            if file_refs["secrets"] or file_refs["vars"]:
                self.results[file_path.name] = file_refs

        except Exception:
            # Ignore invalid yaml or read errors
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
    def __init__(self, repo: str = None):
        if not shutil.which("gh"):
            raise click.ClickException(
                "GitHub CLI ('gh') not found. Please install it."
            )

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
        full_cmd = ["gh"] + cmd + self.repo_arg + ["--json", "name"]
        try:
            res = subprocess.run(full_cmd, capture_output=True, text=True, check=True)
            return json.loads(res.stdout)
        except subprocess.CalledProcessError:
            return []

    def fetch_data(self):
        click.echo("🔄 Fetching definitions from GitHub...")

        # Repo level
        repo_secrets = set(x["name"] for x in self._run_json(["secret", "list"]))
        repo_vars = set(x["name"] for x in self._run_json(["variable", "list"]))

        # Environment level
        envs = {}
        # Fetch env list via API
        api_cmd = (
            ["gh", "api"]
            + self.repo_arg
            + ["repos/:owner/:repo/environments", "--jq", ".environments[].name"]
        )
        res = subprocess.run(api_cmd, capture_output=True, text=True)

        if res.returncode == 0 and res.stdout.strip():
            env_names = res.stdout.strip().split("\n")
            for env in env_names:
                if not env:
                    continue
                e_secrets = set(
                    x["name"] for x in self._run_json(["secret", "list", "--env", env])
                )
                e_vars = set(
                    x["name"]
                    for x in self._run_json(["variable", "list", "--env", env])
                )
                envs[env] = {"secrets": e_secrets, "vars": e_vars}

        return {"repo_secrets": repo_secrets, "repo_vars": repo_vars, "envs": envs}


# --- REPORTING ---


def generate_output(scanner, gh_data):
    """Generates a list of strings for the report."""
    lines = []

    def h1(text):
        lines.append(f"\n{'=' * 60}\n {text}\n{'=' * 60}")

    def h2(text):
        lines.append(f"\n--- {text} ---")

    def line(text):
        lines.append(text)

    h1("GITHUB WORKFLOW CONFIG AUDIT")

    # 1. Global Summary
    req_vars = sorted(scanner.global_usage["vars"])
    req_secrets = sorted(scanner.global_usage["secrets"])

    line(f"Total Unique Vars Found:    {len(req_vars)}")
    line(f"Total Unique Secrets Found: {len(req_secrets)}")

    def get_status(name, kind, repo_set, env_map):
        if not gh_data:
            return ""

        # Check Repo Level
        if name in repo_set:
            return " [✅ Repo]"

        # Check Env Level
        found_envs = [e for e, data in env_map.items() if name in data[kind]]
        if found_envs:
            return f" [⚠️  Envs: {', '.join(found_envs)}]"

        if name == "GITHUB_TOKEN" and kind == "secrets":
            return " [🤖 Auto]"

        return " [❌ MISSING]"

    if req_vars:
        h2("VARIABLES (vars.*)")
        for v in req_vars:
            status = get_status(
                v,
                "vars",
                gh_data.get("repo_vars", set()) if gh_data else set(),
                gh_data.get("envs", {}) if gh_data else {},
            )
            line(f"  - {v:<30}{status}")

    if req_secrets:
        h2("SECRETS (secrets.*)")
        for s in req_secrets:
            status = get_status(
                s,
                "secrets",
                gh_data.get("repo_secrets", set()) if gh_data else set(),
                gh_data.get("envs", {}) if gh_data else {},
            )
            line(f"  - {s:<30}{status}")

    # 2. File Detail
    h2("USAGE BY FILE")
    for fname, refs in scanner.results.items():
        line(f"📄 {fname}")
        if refs["vars"]:
            line(f"   vars    : {', '.join(sorted(refs['vars']))}")
        if refs["secrets"]:
            line(f"   secrets : {', '.join(sorted(refs['secrets']))}")

    # 3. Unused (Bonus)
    if gh_data:
        h2("UNUSED REPO DEFINITIONS")
        unused_v = gh_data["repo_vars"] - set(req_vars)
        unused_s = gh_data["repo_secrets"] - set(req_secrets)

        if unused_v:
            line(f"Unused Repo Vars:    {', '.join(sorted(unused_v))}")
        if unused_s:
            line(f"Unused Repo Secrets: {', '.join(sorted(unused_s))}")
        if not unused_v and not unused_s:
            line("All repo-level definitions are used in the scanned files.")

    return "\n".join(lines)


# --- CLI ENTRY POINT ---


@click.command()
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--check-gh",
    is_flag=True,
    help="Fetch definitions from GitHub to compare existence.",
)
@click.option(
    "--repo",
    default=None,
    help="Target GitHub repo (owner/repo). Default: current git remote.",
)
@click.option("--out", type=click.Path(), help="Write summary to this text file.")
def cli(path, check_gh, repo, out):
    """
    Scans GitHub Workflow YAML files in PATH to find 'vars.*' and 'secrets.*'.

    PATH can be a single .yml file or a directory (recursive scan).
    """

    # 1. Scan Local Files
    scanner = WorkflowScanner()
    scanner.scan_path(path)

    if not scanner.results:
        click.secho("No variables or secrets found in the specified path.", fg="yellow")
        return

    # 2. Fetch GitHub Data (Optional)
    gh_data = None
    if check_gh:
        try:
            inspector = GHInspector(repo)
            gh_data = inspector.fetch_data()
        except click.ClickException as e:
            click.secho(str(e), fg="red")
            sys.exit(1)

    # 3. Generate Report
    report_text = generate_output(scanner, gh_data)

    # 4. Print to Stdout (with colors for status)
    # We print the raw report, but let's colorize specific markers if printing to terminal
    for line in report_text.split("\n"):
        if "[❌ MISSING]" in line:
            click.secho(line, fg="red")
        elif "[✅ Repo]" in line:
            click.secho(line, fg="green")
        elif "[⚠️" in line:
            click.secho(line, fg="yellow")
        elif line.startswith("="):
            click.secho(line, bold=True)
        else:
            click.echo(line)

    # 5. Write to File
    if out:
        try:
            with open(out, "w", encoding="utf-8") as f:
                f.write(report_text)
            click.secho(f"\n💾 Report saved to: {out}", fg="blue")
        except Exception as e:
            click.secho(f"Error writing file: {e}", fg="red")


if __name__ == "__main__":
    cli()
