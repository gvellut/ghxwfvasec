import click

from .account import account
from .project import project


@click.group()
def main():
    """List GitHub Actions settings and audit workflow references."""


main.add_command(project)
main.add_command(account)


if __name__ == "__main__":
    main()
