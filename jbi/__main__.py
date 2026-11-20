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


@cli.command()
@click.option("--tag", default=None, help="Only this action's whiteboard tag.")
@click.option(
    "--days",
    type=int,
    default=30,
    show_default=True,
    help="Only bugs changed in the last N days; 0 for all.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["json", "md"]),
    default="json",
    show_default=True,
)
@click.option("--output", type=click.Path(dir_okay=False), default=None)
def reconcile(tag, days, output_format, output):
    """Report where BMO and Jira have drifted apart (read-only, R-13)."""
    import logging

    from jbi import reconcile as rec
    from jbi.bugzilla.service import get_service as get_bugzilla_service
    from jbi.environment import get_settings
    from jbi.identity import get_identity_map
    from jbi.jira import get_service as get_jira_service
    from jbi.log import CONFIG

    logging.config.dictConfig(CONFIG)
    settings = get_settings()
    report = rec.build_report(
        get_actions(),
        get_bugzilla_service().client,
        get_jira_service().client,
        get_identity_map(),
        since=rec.since_days(days or None),
        rule_jql=settings.jira_inbound_rule_jql,
        only_tag=tag,
    )
    rec.publish(report)
    text = rec.to_markdown(report) if output_format == "md" else rec.to_json(report)
    if output:
        with open(output, "w", encoding="utf8") as file:
            file.write(text)
        click.echo(f"{len(report.findings)} finding(s); report written to {output}")
    else:
        click.echo(text)


if __name__ == "__main__":
    cli()
