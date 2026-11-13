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


@cli.command()
@click.option(
    "--timeout",
    type=int,
    default=None,
    help="Seconds to listen before exiting (default: PUBSUB_PULL_TIMEOUT_SECONDS).",
)
def consume(timeout):
    """Pull events from Pub/Sub and run them through the ingest seam.

    Runs as its own process alongside the web service, which keeps serving
    the webhook endpoints and health checks.
    """
    import logging

    from jbi.consumer import Consumer
    from jbi.log import CONFIG

    logging.config.dictConfig(CONFIG)
    Consumer().run(timeout_seconds=timeout)


if __name__ == "__main__":
    cli()
