When looking to add a new whiteboard tag with downstream mapping follow the format below:

## Minimal Config:
```json
{
    "whiteboard_tag": "wb_tag",
    "jira_project_key": "WBTAG",
}
```

### Caveats:
- using the default action requires the `whiteboard_tag` and `jira_project_key` fields
    - "action" defaults to "default_action"
    - "enabled" defaults to true
- there is check to confirm the `whiteboard_tag` is also the filename (`whiteboard_tag`.json)
    - the above example is expected to exist in a file named: "wb_tag.json"
    - the below example is expected to exist in a file named: "example.json"
- `whiteboard_tag` will be used to sync bugs to their proper downstream boards
    - the tag is expected to exist in a bug in the following format: [`whiteboard_tag`-...]
    - the above example would be sync'd to `WBTAG` when the tag [wb_tag-something] is found on a bug
    - the below example would be sync'd to `EXMPL` when the tag [example-x1y2z3] is found on a bug
- Additional fields can be added into the configuration if desired.
    - the below json adds a field "example_field" that is likely to be used in the "example_action"

## Advanced Config:
```json
{
    "enabled": false,
    "action": "example_action",
    "whiteboard_tag": "example",
    "jira_project_key": "EXMPL",
    "example_field": {
        "yes": 1,
        "no": 0
    }
}
```
