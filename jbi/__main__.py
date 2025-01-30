import click

from jbi.configuration import get_actions

@click.group()
def cli():
    pass

@cli.command()
@click.argument("env", default="prod")
def lint(env):
    click.echo(f"Linting: {env} configuration")

    get_actions(env)
    click.secho("No issues found.", fg="green")

if __name__ == "__main__":
    cli()
