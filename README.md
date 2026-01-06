# ghxwfvasec

`ghxwfvasec` = **GitHub extract workflow vars/secrets**.

It scans GitHub Actions workflow YAML files and extracts references to:
- `vars.NAME`
- `secrets.NAME`

Optionally (with `--check-gh`), it uses the GitHub CLI (`gh`) to fetch the repo-level and environment-level variables/secrets that exist in GitHub and reports which referenced names are missing or inherited.

## Install (local)

This package is not on PyPI. After cloning, install with:

```bash
uv sync
```

## Use of gh

Instead of using a custom PAT, the package uses the `gh` command-line to connect to Github and extract the var/secret names. That tool should be available and authentication set up for accessing the repositories you want to audit.

Refer to [the `gh` doc](https://cli.github.com/manual/) for installation and setup.

## Usage

Scan workflow files under a directory (or a single file):

```bash
uv run ghxwfvasec .github/workflows
```

Audit against GitHub (requires `gh` installed and authenticated with `gh auth login`):

```bash
uv run ghxwfvasec .github/workflows --check-gh --repo owner/repo
```

Filter the report to specific environments:

```bash
# include repo-level ("actions") plus one environment
uv run ghxwfvasec .github/workflows --check-gh --repo owner/repo -e actions -e production
```

Write the report to a file:

```bash
uv run ghxwfvasec .github/workflows --out report.txt
```