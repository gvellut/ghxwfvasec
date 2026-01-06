import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml
from github import Github


class WorkflowAuditor:
    def __init__(self):
        # Regex to find secrets.NAME or vars.NAME
        # Catches: ${{ secrets.MY_SECRET }}, secrets.MY_SECRET (in if clauses), etc.
        self.pattern = re.compile(r"\b(secrets|vars)\.([a-zA-Z0-9_-]+)\b")
        self.results = {}
        self.global_usage = {"secrets": set(), "vars": set()}

    def _extract_from_string(self, text: str) -> None:
        matches = self.pattern.findall(text)
        for context, name in matches:
            # Skip built-in GITHUB_TOKEN if you prefer, usually valid to keep
            self.global_usage[context].add(name)
            self.current_file_refs[context].add(name)

    def _walk_node(self, node: Any) -> None:
        """Recursively walk the YAML tree to find strings."""
        if isinstance(node, dict):
            for value in node.values():
                self._walk_node(value)
        elif isinstance(node, list):
            for item in node:
                self._walk_node(item)
        elif isinstance(node, str):
            self._extract_from_string(node)

    def parse_file(self, file_path: Path) -> None:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                # We load the YAML to avoid parsing commented-out code
                data = yaml.safe_load(f)

            if not data:
                return

            self.current_file_refs = {"secrets": set(), "vars": set()}
            self._walk_node(data)

            self.results[file_path.name] = self.current_file_refs

        except Exception as e:
            print(f"Error parsing {file_path}: {e}", file=sys.stderr)

    def scan_path(self, path: str) -> None:
        p = Path(path)
        files = []
        if p.is_file():
            files = [p]
        elif p.is_dir():
            # valid extensions for GH workflows
            files = list(p.glob("**/*.yml")) + list(p.glob("**/*.yaml"))

        for f in files:
            self.parse_file(f)


class GithubConnector:
    def __init__(self, token: str, repo_name: str):
        self.g = Github(token)
        try:
            self.repo = self.g.get_repo(repo_name)
        except Exception as e:
            print(f"Error connecting to repo {repo_name}: {e}")
            sys.exit(1)

    def fetch_definitions(self):
        print(f"Connecting to GitHub Repo: {self.repo.full_name}...")

        # 1. Repo Secrets
        repo_secrets = set(s.name for s in self.repo.get_secrets())

        # 2. Repo Variables
        repo_vars = set(v.name for v in self.repo.get_variables())

        # 3. Environment Secrets/Vars (Optional: Loop through environments)
        envs = {}
        for env in self.repo.get_environments():
            e_secrets = set(s.name for s in env.get_secrets())
            e_vars = set(v.name for v in env.get_variables())
            envs[env.name] = {"secrets": e_secrets, "vars": e_vars}

        return {"repo_secrets": repo_secrets, "repo_vars": repo_vars, "envs": envs}


def generate_report(
    auditor: WorkflowAuditor, gh_data: dict = None, output_file: str = None
):
    lines = []

    def add(line=""):
        print(line)
        lines.append(line)

    add("=" * 60)
    add(" GITHUB WORKFLOW VARIABLE AUDIT")
    add("=" * 60)

    # 1. Per File Breakdown
    for filename, refs in auditor.results.items():
        if not refs["secrets"] and not refs["vars"]:
            continue
        add(f"\n📄 File: {filename}")
        if refs["vars"]:
            add(f"   [vars]    : {', '.join(sorted(refs['vars']))}")
        if refs["secrets"]:
            add(f"   [secrets] : {', '.join(sorted(refs['secrets']))}")

    # 2. Global Summary
    add("\n" + "=" * 60)
    add(" GLOBAL USAGE SUMMARY (Required by YML files)")
    add("=" * 60)

    req_vars = sorted(auditor.global_usage["vars"])
    req_secrets = sorted(auditor.global_usage["secrets"])

    add(f"Total Unique Vars Needed:    {len(req_vars)}")
    add(f"Total Unique Secrets Needed: {len(req_secrets)}")
    add("-" * 30)

    if req_vars:
        add("VARS used:")
        for v in req_vars:
            status = ""
            if gh_data and v not in gh_data["repo_vars"]:
                # Check envs if not in repo
                found_in_env = [e for e, d in gh_data["envs"].items() if v in d["vars"]]
                if found_in_env:
                    status = f" (Found in Envs: {', '.join(found_in_env)})"
                else:
                    status = " ❌ [MISSING in Repo]"
            elif gh_data:
                status = " ✅ [Defined in Repo]"
            add(f"  - vars.{v}{status}")

    if req_secrets:
        add("\nSECRETS used:")
        for s in req_secrets:
            if s == "GITHUB_TOKEN":
                add(f"  - secrets.{s} (Automatic)")
                continue

            status = ""
            if gh_data and s not in gh_data["repo_secrets"]:
                found_in_env = [
                    e for e, d in gh_data["envs"].items() if s in d["secrets"]
                ]
                if found_in_env:
                    status = f" (Found in Envs: {', '.join(found_in_env)})"
                else:
                    status = " ❌ [MISSING in Repo]"
            elif gh_data:
                status = " ✅ [Defined in Repo]"
            add(f"  - secrets.{s}{status}")

    # 3. Unused definitions (Bonus)
    if gh_data:
        add("\n" + "=" * 60)
        add(" UNUSED DEFINITIONS (Defined in GitHub but not in scanned YML)")
        add("=" * 60)
        unused_vars = gh_data["repo_vars"] - set(req_vars)
        unused_secrets = gh_data["repo_secrets"] - set(req_secrets)

        if unused_vars:
            add(f"Unused Repo Vars: {', '.join(unused_vars)}")
        if unused_secrets:
            add(f"Unused Repo Secrets: {', '.join(unused_secrets)}")

    # Write to file
    if output_file:
        try:
            with open(output_file, "w") as f:
                f.write("\n".join(lines))
            print(f"\n💾 Summary written to: {output_file}")
        except Exception as e:
            print(f"\nCould not write output file: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Extract vars and secrets from GitHub Workflows"
    )
    parser.add_argument("path", help="Path to .yml file or folder containing workflows")
    parser.add_argument(
        "--repo",
        help="GitHub repo (format: owner/repo) to check existence",
        default=None,
    )
    parser.add_argument(
        "--token",
        help="GitHub PAT (optional if using --repo, defaults to env GITHUB_TOKEN)",
        default=None,
    )
    parser.add_argument("--out", help="Output text file path", default=None)

    args = parser.parse_args()

    # 1. Scan Files
    auditor = WorkflowAuditor()
    auditor.scan_path(args.path)

    # 2. Fetch GitHub Data (if requested)
    gh_data = None
    if args.repo:
        token = args.token or os.getenv("GITHUB_TOKEN")
        if not token:
            print(
                "Error: --repo specified but no token found. Use --token or set GITHUB_TOKEN env var."
            )
            sys.exit(1)
        connector = GithubConnector(token, args.repo)
        gh_data = connector.fetch_definitions()

    # 3. Report
    generate_report(auditor, gh_data, args.out)


if __name__ == "__main__":
    main()
