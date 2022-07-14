## Looking to add a new action?
"actions" are Python modules that, if available in the `PYTHONPATH`,
can be run by updating the `module` attribute of the YAML config file.

### Create a new action...
Let's create a `new_action`!
1. First, add a new Python file (eg. `my_team_action.py`) in the `src/jbi/whiteboard_actions/` directory.
1. Add the Python function `init` to the module, for example:
    ```python
    def init(jira_project_key, optional_param=42):
        return lambda payload: print(f"{optional_param}, going to {jira_project_key}!")
    ```
    1. In the above example the `jira_project_key` parameter is required
    1. `optional_param`, which has a default value, is not required to run this action
    1. `init()` returns a `__call__`able object that the system calls with the Bugzilla request payload
1. Use the `payload` to perform the desired processing!
1. Use the available service calls from `src/jbi/services.py' (or make new ones)
1. Update the `README.md` to document your action
1. Now the action `src.jbi.whiteboard_actions.my_team_actions` can be used in the YAML configuration, under the `module` key.
