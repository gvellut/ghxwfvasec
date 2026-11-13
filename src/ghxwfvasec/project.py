import subprocess
import sys
from pathlib import Path

import click

from .github import GHInspector
from .report import ACTIONS_ENV, generate_output, write_report
from .scanner import WorkflowScanner


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
def project(path, auto, check_gh, repo, out, envs):
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
            inspector.check_auth()
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

    write_report(report_text, out)
