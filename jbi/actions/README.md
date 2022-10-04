## Looking to add a new action?

"actions" are Python modules that, if available in the `PYTHONPATH`,
can be run by updating the `module` attribute of the YAML config file.

### Create a new action...

Let's create a `new_action`!

1. First, add a new Python file (eg. `my_team_action.py`) in the `jbi/actions/` directory.
1. Add the Python function `init` to the module, for example:

   ```python
   from jbi import ActionResult, Operation
   from jbi.models import ActionContext

   JIRA_REQUIRED_PERMISSIONS = {"CREATE_ISSUES"}

   def init(jira_project_key, optional_param=42):

       def execute(context: ActionContext) -> ActionResult:
           print(f"{optional_param}, going to {jira_project_key}!")
           return True, {"result": 42}

       return execute
   ```

   1. In the above example the `jira_project_key` parameter is required
   1. `optional_param`, which has a default value, is not required to run this action
   1. `init()` returns a `__call__`able object that the system calls with the `ActionContext` object
   1. The returned `ActionResult` features a boolean to indicate whether something was performed or not, along with a `Dict` (used as a response to the WebHook endpoint).

1. Use the `context.bug`, `context.event`, `context.jira` information to perform the desired processing!
1. List the required Jira permissions to be set on projects that will use this action in the `JIRA_REQUIRED_PERMISSIONS` constant. The list of built-in permissions is [available on Atlassian API docs](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-permission-schemes/#built-in-permissions).
1. Use the available service calls from `jbi/services` (or make new ones)
1. Update the `README.md` to document your action
1. Now the action `jbi.actions.my_team_actions` can be used in the YAML configuration, under the `module` key.
