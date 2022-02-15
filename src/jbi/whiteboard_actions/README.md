## Looking to add a new action?
"actions" are python functions that, once added into `whiteboard_actions/__init__.py` (from a new module),
can be run by modifying the json config file to call the new named actions.

# When an action is triggered...
Let's create a `new_action`!
1. First, add a new python file called "my_team_actions".
1. Add a python function to new "my_team_actions" module, let's use the following:
    ```python
    def unique_process(data,context):
        #stuff
    ```
1. Using `data` and `context` perform the desired processing "stuff"!
    1. `data` is webhook payload
    1. `context` is the full json blob (if additional context is desired)
    1. Use the available service calls from `src/jbi/services.py'
1. Add the following to `src/jbi/whiteboard_actions/__init__.py`:
    1. `from .my_team_actions import *`
1. Now the action `unique_process` can be used.
