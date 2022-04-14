## Local Setup

_Note: [Environment variables are converted to, and used as, a python Settings object through pydantic.](src/app/environment.py)_

----
#### Jira API Secrets

Jira Test Env can be found here:
- https://mozit-test.atlassian.net/jira/projects

Jira API Token can be generated here (in account settings):
- https://id.atlassian.com/manage-profile/security/api-tokens

After generating an API token, set the associated environment variables:
- `JIRA_USERNAME` = `"fake_jira_username@allizom.com"`
- `JIRA_PASSWORD` = `"fake_jira_api_key"`

Confirm the following environment variable is the expected value:
- `JIRA_BASE_URL` (modify this to the correct Jira Server if not using Mozilla Jira test instance.)

----

#### Bugzilla API Secrets

Bugzilla Dev Env can be found here:
- https://bugzilla-dev.allizom.org/home

Bugzilla API Key can be generated here (in user preferences):
- https://bugzilla-dev.allizom.org/userprefs.cgi?tab=apikey

After generating an API token, set the associated environment variables:
- `BUGZILLA_API_KEY` = `"fake_bugzilla_api_key"`

Confirm the following environment variable is the expected value:
- `BUGZILLA_BASE_URL` (modify this to the correct Bugzilla Server if not using Mozilla Bugzilla Dev instance.)

----

#### Action Configuration

The `ENV` environment variable defaults to `dev` and as such uses the `config/config.dev.yaml`

Additional/different action configuration YAMLs can be constructed and used through this environment variable, the YAML used for the service is in the form: `config/config.{ENV}.yaml`

----
#### Starting up the Service

After setting the expected Environment variables, the service can be started with `make start`.

Using `ngrok`, `localtunnel`, or another alternate tool the locally run service can be used to accept webhooks and provide updates to the chosen jira instance.
The chosen tool should provide a publicly accessible endpoint that can be used in the next step.

----

#### Setting up Bugzilla Webhooks

Set up the webhooks within bugzilla preferences:
- https://bugzilla-dev.allizom.org/userprefs.cgi?tab=webhooks

Minimum required fields to setup a webhook:
- `Name`:
    - Will be provided in the request
- `URL`:
    - The URL the requests will POST to
- `Events`:
    - Selected bugzilla events (create, update, comment, attachment)
- `Product`:
    - Currently needs to be set
- `Component`:
    - Can be set to ANY, or a Specific Component.
