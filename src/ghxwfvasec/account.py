import json
import subprocess
from urllib.parse import urlsplit

import click

from .github import GHInspector, require_gh
from .report import report_header, write_report


def inventory_lines(gh_data, matches: tuple[str, ...]) -> tuple[list[str], bool]:
    envs = gh_data["envs"]
    scopes = [
        ("(Actions)", {"vars": gh_data["repo_vars"], "secrets": gh_data["repo_secrets"]})
    ]
    scopes.extend(
        (f"(Environment) {env}", definitions)
        for env, definitions in sorted((envs or {}).items())
    )

    lines = []
    complete = envs is not None
    for kind, title in (("vars", "VARIABLES"), ("secrets", "SECRETS")):
        locations_by_name = {}
        for location, definitions in scopes:
            names = definitions[kind]
            if names is None:
                complete = False
                continue
            for name in names:
                if not matches or any(match in name.casefold() for match in matches):
                    locations_by_name.setdefault(name, []).append(location)

        if locations_by_name:
            lines.append(f"\n--- {title} ---")
            for name, locations in sorted(locations_by_name.items()):
                lines.append(f"  • {name}")
                lines.extend(f"    |__ {location}" for location in locations)

    return lines, complete


@click.command()
@click.option(
    "-a",
    "--all",
    "show_all",
    is_flag=True,
    help="Show repositories with no matching variables or secrets.",
)
@click.option(
    "--match",
    "matches",
    multiple=True,
    metavar="TEXT",
    help="Match name substrings without regard to case. Repeat to match any substring.",
)
@click.option("--out", type=click.Path(dir_okay=False), help="Write summary to file.")
def account(show_all, matches, out):
    """List configured Actions variables and secrets for the active gh account."""
    require_gh()
    try:
        result = subprocess.run(
            [
                "gh", "api", "user/repos?affiliation=owner&per_page=100",
                "--paginate", "--slurp",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
    except subprocess.CalledProcessError as error:
        raise click.ClickException(
            f"Could not list repositories for the active gh account. {error.stderr.strip()}"
        ) from error

    pages = json.loads(result.stdout)
    repos = sorted(
        (repo for page in pages for repo in page), key=lambda repo: repo["full_name"]
    )
    matches = tuple(match.casefold() for match in matches)
    reports = []
    skipped_incomplete = 0
    with click.progressbar(
        repos, label="Listing Repository Settings", file=click.get_text_stream("stderr")
    ) as bar:
        for repo in bar:
            name = repo["full_name"]
            host = urlsplit(repo["html_url"]).netloc
            gh_data = GHInspector(name, host=host).fetch_data(
                show_progress=False, warn_on_error=True
            )
            lines, complete = inventory_lines(gh_data, matches)
            if not lines and not show_all:
                if not complete:
                    skipped_incomplete += 1
                continue

            if not complete:
                lines.append("Some GitHub settings could not be listed.")
            elif not lines:
                lines.append(
                    "No matching variable or secret." if matches else "No variable or secret."
                )
            reports.append("\n".join([report_header(name), *lines]))

    if reports:
        report = "\n".join(reports)
    elif matches:
        report = "No matching variables or secrets found."
    else:
        report = "No variables or secrets found."
    if skipped_incomplete:
        report += f"\n\nRepositories with unreadable settings and no results: {skipped_incomplete}"
    write_report(report, out)
