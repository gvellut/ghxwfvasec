import json
import shutil
import subprocess
from typing import List, Optional

import click


def require_gh():
    if not shutil.which("gh"):
        raise click.ClickException("GitHub CLI ('gh') not found. Please install it.")


class GHInspector:
    def __init__(self, repo: Optional[str] = None, *, host: Optional[str] = None):
        self.repo_str = repo
        self.host = host
        # -R is valid for 'gh secret ...' commands but NOT 'gh api'
        target = f"{host}/{repo}" if host and repo else repo
        self.repo_arg = ["-R", target] if target else []

    def check_auth(self):
        require_gh()
        try:
            subprocess.run(["gh", "auth", "status"], check=True, capture_output=True)
        except subprocess.CalledProcessError:
            raise click.ClickException(
                "You are not logged into gh CLI. Run 'gh auth login'."
            )

    def _names(self, cmd: List[str], *, warn_on_error: bool) -> Optional[set[str]]:
        # Helper for standard gh commands (secret list, etc) which support -R
        full_cmd = ["gh"] + cmd + self.repo_arg + ["--json", "name"]
        try:
            res = subprocess.run(full_cmd, capture_output=True, text=True, check=True)
            return {item["name"] for item in json.loads(res.stdout)}
        except subprocess.CalledProcessError as e:
            if warn_on_error:
                click.secho(
                    f"Warning: {self.repo_str}: gh {' '.join(cmd)} failed. "
                    f"{e.stderr.strip()}",
                    fg="yellow",
                    err=True,
                )
                return None
            return set()

    def fetch_data(self, *, show_progress=True, warn_on_error=False):
        if show_progress:
            click.echo("🔄 Fetching definitions from GitHub...")

        # 1. Fetch Repo Level
        repo_secrets = self._names(["secret", "list"], warn_on_error=warn_on_error)
        repo_vars = self._names(["variable", "list"], warn_on_error=warn_on_error)

        # 2. Fetch Environment List
        # FIX: Construct the endpoint manually to avoid passing -R to gh api
        if self.repo_str:
            endpoint = f"repos/{self.repo_str}/environments"
        else:
            # Use gh magic placeholders for current dir context
            endpoint = "repos/:owner/:repo/environments"

        api_cmd = ["gh", "api", endpoint, "--paginate", "--jq", ".environments[].name"]
        if self.host:
            api_cmd.extend(["--hostname", self.host])

        envs = {}
        try:
            res = subprocess.run(api_cmd, capture_output=True, text=True, check=True)
            env_names = res.stdout.splitlines()
        except subprocess.CalledProcessError as e:
            repo_context = f" for {self.repo_str}" if self.repo_str else ""
            click.secho(
                f"Warning: Failed to fetch environments{repo_context} via API: {e.stderr}",
                fg="yellow",
                err=warn_on_error,
            )
            env_names = []
            if warn_on_error:
                envs = None

        # 3. Fetch Details per Environment
        if env_names:
            with click.progressbar(
                env_names, label="Fetching Envs", hidden=not show_progress
            ) as bar:
                for env in bar:
                    if not env:
                        continue
                    # Note: "secret list" DOES support self.repo_arg
                    e_secrets = self._names(
                        ["secret", "list", "--env", env], warn_on_error=warn_on_error
                    )
                    e_vars = self._names(
                        ["variable", "list", "--env", env], warn_on_error=warn_on_error
                    )
                    envs[env] = {"secrets": e_secrets, "vars": e_vars}

        return {"repo_secrets": repo_secrets, "repo_vars": repo_vars, "envs": envs}
