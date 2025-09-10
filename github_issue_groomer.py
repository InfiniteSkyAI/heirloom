import os
import requests
from datetime import datetime, timedelta

def get_most_recent_child_activity(repo_owner, repo_name, issue_id, token):
    """
    Finds the most recent activity date for an issue's sub-issues.
    Uses the GitHub GraphQL API to traverse the hierarchy efficiently.
    """
    url = "https://api.github.com/graphql"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    query = """
    query($owner: String!, $name: String!, $issue_id: Int!) {
      repository(owner: $owner, name: $name) {
        issue(number: $issue_id) {
          projectItems(first: 100) {
            nodes {
              fieldValues(first: 20) {
                nodes {
                  ... on ProjectV2ItemFieldSingleSelectValue {
                    name
                  }
                  ... on ProjectV2ItemFieldDateValue {
                    date
                  }
                  ... on ProjectV2ItemFieldTextValue {
                    text
                  }
                }
              }
              content {
                ... on Issue {
                  id
                  number
                  title
                  updatedAt
                }
              }
            }
          }
        }
      }
    }
    """
    variables = {
        "owner": repo_owner,
        "name": repo_name,
        "issue_id": issue_id
    }
    
    response = requests.post(url, headers=headers, json={'query': query, 'variables': variables})
    response.raise_for_status()
    data = response.json()
    
    most_recent_date = None

    try:
        nodes = data['data']['repository']['issue']['projectItems']['nodes']
        for node in nodes:
            content = node.get('content')
            if content and content.get('__typename') == 'Issue':
                updated_at_str = content.get('updatedAt')
                if updated_at_str:
                    updated_at = datetime.fromisoformat(updated_at_str.rstrip('Z'))
                    if most_recent_date is None or updated_at > most_recent_date:
                        most_recent_date = updated_at
    except (KeyError, TypeError) as e:
        print(f"Warning: Could not parse GraphQL response for issue {issue_id}: {e}")
        return None
        
    return most_recent_date

def add_comment_to_issue(repo_owner, repo_name, issue_id, token, message):
    """
    Adds a comment to a given issue.
    """
    url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/issues/{issue_id}/comments"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {
        "body": message
    }
    
    response = requests.post(url, headers=headers, json=payload)
    response.raise_for_status()
    print(f"Successfully added comment to issue #{issue_id}.")

def main():
    """
    Main logic for the Heirloom bot.
    """
    # Get inputs from environment variables set by the action
    token = os.environ.get('INPUT_GITHUB-TOKEN')
    repo_owner = os.environ.get('INPUT_REPO-OWNER')
    repo_name = os.environ.get('INPUT_REPO-NAME')
    parent_labels = os.environ.get('INPUT_PARENT-ISSUE-LABELS').split(',')
    parent_types = os.environ.get('INPUT_PARENT-ISSUE-TYPES').split(',')
    days_threshold = int(os.environ.get('INPUT_DAYS-THRESHOLD'))
    update_message = os.environ.get('INPUT_UPDATE-MESSAGE')

    if not all([token, repo_owner, repo_name]):
        print("Missing required inputs. Please check your workflow configuration.")
        exit(1)

    if not parent_labels and not parent_types:
        print("At least one of 'parent-issue-labels' or 'parent-issue-types' must be provided.")
        exit(1)

    print(f"Starting Heirloom bot for {repo_owner}/{repo_name}...")

    # Construct the GraphQL query dynamically
    query_filters = []
    if parent_labels and parent_labels[0]:
        query_filters.append(f'labels: [{",".join([f'"{label.strip()}"' for label in parent_labels])}]')
    if parent_types and parent_types[0]:
        query_filters.append(f'issueTypes: [{",".join([f'{issue_type.strip()}' for issue_type in parent_types])}]')
    
    issues_filter_string = ", ".join(query_filters)

    query_parent_issues = f"""
    query {{
      repository(owner: "{repo_owner}", name: "{repo_name}") {{
        issues(first: 50, {issues_filter_string}, states: OPEN) {{
          nodes {{
            id
            number
            title
            updatedAt
          }}
        }}
      }}
    }}
    """
    
    url = "https://api.github.com/graphql"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    response = requests.post(url, headers=headers, json={'query': query_parent_issues})
    response.raise_for_status()
    data = response.json()
    
    try:
        parent_issues = data['data']['repository']['issues']['nodes']
    except (KeyError, TypeError) as e:
        print(f"Warning: Could not fetch parent issues: {e}")
        return

    for issue in parent_issues:
        issue_number = issue['number']
        print(f"Processing parent issue: #{issue_number} - {issue['title']}")

        most_recent_child_activity = get_most_recent_child_activity(repo_owner, repo_name, issue_number, token)
        
        if most_recent_child_activity:
            current_date = datetime.now()
            days_since_child_activity = (current_date - most_recent_child_activity).days
            
            if days_since_child_activity < days_threshold:
                # Add a comment to the parent issue to update its `updated_at` time
                try:
                    add_comment_to_issue(repo_owner, repo_name, issue_number, token, update_message)
                except requests.exceptions.RequestException as e:
                    print(f"Error adding comment to issue #{issue_number}: {e}")
            else:
                print(f"No recent child activity for issue #{issue_number}. It will be handled by the stale bot.")
        else:
            print(f"No sub-issues found or could not determine activity for issue #{issue_number}.")
            
if __name__ == "__main__":
    main()
