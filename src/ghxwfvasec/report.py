from typing import Optional, Tuple

import click

# Constant for the "Actions" (repo-level) environment filter
ACTIONS_ENV = "actions"


def report_header(title):
    return f"\n{'=' * 60}\n {title}\n{'=' * 60}"


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

    def h2(text):
        lines.append(f"\n--- {text} ---")

    def line(text):
        lines.append(text)

    line(report_header("GITHUB WORKFLOW CONFIG AUDIT"))

    req_vars = sorted(scanner.global_usage["vars"])
    req_secrets = sorted(scanner.global_usage["secrets"])

    line(f"Total Unique Vars Found:    {len(req_vars)}")
    line(f"Total Unique Secrets Found: {len(req_secrets)}")

    env_map = gh_data.get("envs", {}) if gh_data else {}
    if env_map is None:
        line("Deployment environments could not be listed.")
    available_envs = sorted(env_map or {})

    # Determine which environments to show
    show_actions = filter_envs is None or ACTIONS_ENV in filter_envs
    if filter_envs is None:
        envs_to_show = available_envs
    else:
        envs_to_show = [e for e in available_envs if e in filter_envs]

    def analyze_item(name, kind, repo_set):
        # 1. Check Repo Level
        is_in_repo = repo_set is not None and name in repo_set

        # Special case: GITHUB_TOKEN is auto-generated
        if name == "GITHUB_TOKEN" and kind == "secrets":
            return " [🤖 Auto]", dict.fromkeys(available_envs, " [🤖 Auto]")

        if repo_set is None:
            repo_status = " [⚠️ Unknown]"
        else:
            repo_status = " [✅ Repo]" if is_in_repo else " [❌ MISSING]"

        # 2. Check Env Level
        env_status = {}
        for env_name in available_envs:
            env_data = env_map[env_name].get(kind, set())
            if env_data is None:
                env_status[env_name] = " [⚠️ Unknown]"
            elif name in env_data:
                env_status[env_name] = " [✅ Env]"
            elif is_in_repo:
                # GitHub Actions inherits repo secrets if not overridden
                env_status[env_name] = " [✅ Inherited]"
            elif repo_set is None:
                env_status[env_name] = " [⚠️ Unknown]"
            else:
                env_status[env_name] = " [❌ MISSING]"

        return repo_status, env_status

    def print_section(title, items, kind, repo_set):
        if not items:
            return
        h2(title)

        for item in items:
            line(f"  • {item}")
            if gh_data:
                repo_status, env_status = analyze_item(item, kind, repo_set)
                if show_actions:
                    line(f"    |__ {'(Actions)':<15} {repo_status}")
                for env in envs_to_show:
                    line(f"    |__ {env:<15} {env_status[env]}")
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


def write_report(report_text, out):
    # Print with color parsing
    for line in report_text.split("\n"):
        color = None
        if "[❌ MISSING]" in line:
            color = "red"
        elif "[⚠️ Unknown]" in line:
            color = "yellow"
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
