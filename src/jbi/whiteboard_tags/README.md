When looking to add a new whiteboard tag with downstream mapping follow the format below:

```json
{
    "enabled": false,
    "action": "example_action",
    "whiteboard_tag": "example",
    "jira_project_key": "EXMPL",
}
```

## Caveats:
- the default action requires the `whiteboard_tag` and `jira_project_key` fields
- there is check to confirm the `whiteboard_tag` is also the filename (`whiteboard_tag`.json)
    - the above example is expected to exist in a file named: "example.json"
- `whiteboard_tag` will be used to sync bugs to their proper downstream boards
    - the tag is expected to exist in a bug in the following format: [`whiteboard_tag`-...]
    - the above example would be sync'd to `EXMPL` when the tag [example-x1y2z3] is found on a bug
