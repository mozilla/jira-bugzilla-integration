import click

from jbi.configuration import get_actions


@click.group()
def cli():
    pass


@cli.command()
@click.argument("env", default="all")
def lint(env):
    click.echo(f"Linting: {env} action configuration")

    if env == "all":
        envs = ["local", "nonprod", "prod"]
    else:
        envs = [env]

    for env in envs:
        get_actions(env)
        click.secho(f"No issues found for {env}.", fg="green")


if __name__ == "__main__":
    cli()
