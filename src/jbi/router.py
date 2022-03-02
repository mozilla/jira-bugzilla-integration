import logging
from typing import Dict, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from src.app import environment
from src.jbi import configuration

api_router = APIRouter(tags=["JBI"])

jbi_logger = logging.getLogger("src.jbi")


def execute_request(request, settings):
    # Is request valid?
    # Is whiteboard known?
    # Execute desired action -- based on whiteboard config
    pass


@api_router.post("/bugzilla_webhook")
def bugzilla_webhook(
    request: Request,
    settings: environment.Settings = Depends(environment.get_settings),
):
    return execute_request(request, settings)


@api_router.get("/whiteboard_tags/")
def get_whiteboard_tag(
    whiteboard_tag: Optional[str] = None,
):
    data = configuration.get_yaml_configurations()
    if whiteboard_tag:
        wb_val = data.get(whiteboard_tag)
        if wb_val:
            data = wb_val
    return data


@api_router.get("/actions/")
def get_actions_by_type(action_type: Optional[str] = None):
    configured_actions = configuration.get_yaml_configurations()
    if action_type:
        data = [
            a["action"]
            for a in configured_actions.values()
            if a["action"].endswith(action_type)
        ]
    else:
        data = [a["action"] for a in configured_actions.values()]
    return data


@api_router.get("/powered_by_jbi", response_class=HTMLResponse)
def powered_by_jbi(enabled: Optional[bool] = None):
    data = configuration.get_yaml_configurations()
    num_configs = len(data)
    html = f"""
    <html>
        <head>
            <title>Powered by JBI</title>
            <style>{get_css_style()}
            </style>
        </head>
        <body>
            <h1>Powered by JBI</h1>
            <p>{num_configs} collections.</p>
            <div id="collections"> {create_inner_html_table(data=data, enable_query=enabled)} </div>
        </body>
    </html>
    """
    return html


def create_inner_html_table(data: Dict, enable_query: Optional[bool]):
    agg_table = ""
    header = """
        <tr>
            <th>Key</th>
            <th>Action</th>
            <th>Contact</th>
            <th>Description </th>
            <th>Enabled</th>
            <th>Parameters</th>
        </tr>
    """
    for key, value in data.items():
        enabled = value.get("enabled")
        if enable_query is None or enabled is enable_query:
            parameters = ", ".join(
                f"{k}={v}" for k, v in value.get("parameters").items()
            )
            per_row = f"""
            <tr>
                <td class="key">{key}</td>
                <td class="action">{value.get("action")}</td>
                <td class="contact">{value.get("contact")}</td>
                <td class="description">{value.get("description")}</td>
                <td class="enabled">{enabled}</td>
                <td class="parameters">{parameters}</td>
            </tr>"""
            agg_table += per_row

    html = f"""
    <table>
        <thead>{header}</thead>
        <tbody class="list">{agg_table}</tbody>
    </table>
    """
    return html


def get_css_style():
    return """body {
      font-family: sans-serif;
    }

    table {
      border-collapse: collapse;
      margin: 25px 0;
      font-size: 0.9em;
      min-width: 400px;
      box-shadow: 0 0 20px rgba(0, 0, 0, 0.15);
    }

    thead tr {
      background-color: #009879;
      color: #ffffff;
      text-align: left;
    }

    th, td {
      padding: 12px 15px;
    }

    tbody tr {
      border-bottom: 1px solid #dddddd;
    }

    tbody tr:nth-of-type(even) {
      background-color: #f3f3f3;
    }

    tbody tr:last-of-type {
      border-bottom: 2px solid #009879;
    }

    .sort:after {
      content: "▼▲";
      padding-left: 10px;
      opacity: 0.5;
    }
    .sort.desc:after {
      content: "▲";
      opacity: 1;
    }
    .sort.asc:after {
      content: "▼";
      opacity: 1;
    }
  """
