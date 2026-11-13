# ghxwfvasec

`ghxwfvasec` = **GitHub extract workflow vars/secrets**.

The `project` command scans GitHub Actions workflow YAML files for variable and secret references.

- `vars.NAME`
- `secrets.NAME`

It uses the GitHub CLI (`gh`) to check referenced names against repository-level Actions settings and deployment environments.

## Install (local)

This package is not on PyPI. After cloning, install with:

```bash
uv sync
```

## Use of gh

Instead of using a custom PAT, the package uses the `gh` command-line to connect to Github and extract the var/secret names. That tool should be available and authentication set up for accessing the repositories you want to audit.

Refer to [the `gh` doc](https://cli.github.com/manual/) for installation and setup.

## Audit a project

By default, `project` does the following.

- Uses `.github/workflows` as the default path if none is provided
- Auto-detects the GitHub repository from the current git directory
- Enables `--check-gh` to fetch GitHub definitions

Simply run from a repository root:

```bash
uv run ghxwfvasec project
```

Or specify a path explicitly:

```bash
uv run ghxwfvasec project .github/workflows
```

To disable auto-detection and GitHub checking:

```bash
uv run ghxwfvasec project .github/workflows --no-auto --no-check-gh
```

Override the auto-detected repo:

```bash
uv run ghxwfvasec project --repo owner/repo
```

Filter the report to specific environments:

```bash
# include repo-level ("actions") plus one environment
uv run ghxwfvasec project .github/workflows --check-gh --repo owner/repo -e actions -e production
```

Write the report to a file:

```bash
uv run ghxwfvasec project .github/workflows --out report.txt
```

## List an account's variables and secrets

`account` lists configured variable and secret names in repositories owned by the active `gh` account. Each name shows where it is configured, either in repository-level Actions settings, in named deployment environments, or in both.

```bash
uv run ghxwfvasec account
uv run ghxwfvasec account -a
uv run ghxwfvasec account --match AWS --match TOKEN --out report.txt
```

Repeat `--match` to include names containing any of the supplied substrings, ignoring case. Use `-a` or `--all` to also show repositories with no matching variables or secrets. `--out` saves a plain-text copy of the report.
