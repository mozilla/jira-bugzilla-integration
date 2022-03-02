## Looking to add a new action?
"actions" are python modules that, if available in the PYTHONPATH,
can be run by updating the `action` attribute of the yaml config file.

### Create a new action...
Let's create a `new_action`!
1. First, add a new python file called `my_team_action.py` in the `src/jbi/whiteboard_actions/` directory.
1. Add the python function `init` to a new "my_team_action" module, let's use the following:
    ```python
    def init(whiteboard_tag, jira_project_key, optional_param=42):
        return lambda payload: print(f"{whiteboard_tag}, going to {jira_project_key}!")
    ```
    1. In the above example `whiteboard_tag` and `jira_project_key` parameters are required
    1. `optional_param`, which has a default value and is not required to run this action
    1. init returns a `__call__`able object that the system calls with the bugzilla request and yaml configuration objects
1. Using `payload` and `context` perform the desired processing!
    1. `payload` is bugzilla webhook payload
    1. Use the available service calls from `src/jbi/services.py' (or make new ones)
1. Now the action `src.jbi.whiteboard_actions.my_team_actions` can be added to a configuration yaml under the `action` key.
