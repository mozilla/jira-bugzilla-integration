## Looking to add a new action?
"actions" are python functions that, once added into `src.jbi.actions.action_list`,
can be run by updating the `action` attribute of the json config file.

### Create a new action...
Let's create a `new_action`!
1. First, add a new python file called "my_team_action" in the `src/jbi/whiteboard_actions/` dir.
1. Add the python function `init` to a new "my_team_action" module, let's use the following:
    ```python
    def init(whiteboard_tag, jira_project_key, optional_param=42):
        return lambda payload, context: print("Hi world!")
    ```
    1. In the above example whiteboard_tag and jira_project_key are required
    1. optional_param, which has a default value--is not required to run this action
    1. init returns a `__call__`able object that the system calls with the bugzilla request and json configuration objects
1. Using `payload` and `context` perform the desired processing!
    1. `payload` is bugzilla webhook payload
    1. `context` is the full json blob (if additional context is desired)
    1. Use the available service calls from `src/jbi/services.py' (or make new ones)
1. Add the module path to `src.jbi.actions.action_list`
1. Now the action `src.jbi.whiteboard_actions.my_team_actions` can be used.
