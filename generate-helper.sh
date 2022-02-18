#!/bin/bash
default_action="default_action"
enabled="true"
read -p "Enter the jira_project_key (required):" JIRA_PROJECT_KEY
read -p "Enter the whiteboard_tag (required):" WHITEBOARD_TAG
read -p "Enter the action (blank=default_action):" ACTION && [[ -z "$ACTION" ]] && ACTION="$default_action"
read -p "Start enabled? (blank=true):" ENABLED && [[ -z "$ENABLED" ]] && ENABLED="$enabled"

echo "Generate file: src/jbi/whiteboard_tags/${WHITEBOARD_TAG}.json"
sed -e "s|@ENABLED@|$ENABLED|g" -e "s|@ACTION@|$ACTION|g" -e "s|@JIRA_PROJECT_KEY@|$JIRA_PROJECT_KEY|g" -e "s|@WHITEBOARD_TAG@|$WHITEBOARD_TAG|g" src/jbi/whiteboard_tags/TEMPLATE > src/jbi/whiteboard_tags/${WHITEBOARD_TAG}.json
