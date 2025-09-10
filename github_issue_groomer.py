import os
import sys
import datetime
from github import Github, GithubException

# --- Configuration (using environment variables from the action.yml) ---
GITHUB_TOKEN = os.environ.get("INPUT_GITHUB-TOKEN")
REPO_OWNER = os.environ.get("INPUT_REPO-OWNER")
REPO_NAME = os.environ.get("INPUT_REPO-NAME")
PARENT_ISSUE_LABELS_STR = os.environ.get("INPUT_PARENT-ISSUE-LABELS", "epic,story")
DAYS_THRESHOLD_STR = os.environ.get("INPUT_DAYS-THRESHOLD", "30")
UPDATE_MESSAGE = os.environ.get("INPUT_UPDATE-MESSAGE", "🤖 This parent issue has been updated due to recent activity on a child issue.")

PARENT_ISSUE_LABELS = [label.strip() for label in PARENT_ISSUE_LABELS_STR.split(',')]

try:
    DAYS_THRESHOLD = int(DAYS_THRESHOLD_STR)
except ValueError:
    print("::error::Invalid value for days-threshold. Must be an integer.")
    sys.exit(1)

# --- GitHub API Initialization ---
if not GITHUB_TOKEN:
    print("::error::GitHub token not found. Please provide a token with 'repo' and 'read:org' scopes.")
    sys.exit(1)

try:
    g = Github(GITHUB_TOKEN)
    repo = g.get_repo(f"{REPO_OWNER}/{REPO_NAME}")
except GithubException as e:
    print(f"::error::Failed to connect to GitHub or repository: {e}")
    sys.exit(1)

def get_parent_issues():
    """
    Fetches open issues that have a parent-issue-label and a sub-issue-progress field.
    Note: The new GitHub Issues fields are not directly searchable via the classic search API,
    but we can filter after fetching.
    """
    print(f"Searching for parent issues with labels: {PARENT_ISSUE_LABELS}")
    query = f"repo:{REPO_OWNER}/{REPO_NAME} is:open is:issue " + " ".join([f"label:\"{label}\"" for label in PARENT_ISSUE_LABELS])
    return g.search_issues(query=query)

def get_child_issues(parent_issue):
    """
    Retrieves sub-issues for a given parent issue using a GraphQL query.
    This is necessary as the REST API v3 doesn't have a direct sub-issue endpoint.
    """
    print(f"Fetching sub-issues for parent issue #{parent_issue.number}...")

    # Define the GraphQL query to get the sub-issues.
    # We need to find the parent issue's ID first.
    query_issue_id = f"""
    query {{
      repository(owner: "{REPO_OWNER}", name: "{REPO_NAME}") {{
        issue(number: {parent_issue.number}) {{
          id
        }}
      }}
    }}
    """
    try:
        issue_id_result = g.graphql_api(query_issue_id)
        parent_issue_id = issue_id_result['repository']['issue']['id']
    except Exception as e:
        print(f"::warning::Could not find GraphQL ID for issue #{parent_issue.number}. Skipping.")
        return []

    # Now use that ID to find all linked child issues.
    query_children = f"""
    query {{
      node(id: "{parent_issue_id}") {{
        ... on Issue {{
          children(first: 100) {{
            nodes {{
              ... on Issue {{
                number
                updatedAt
              }}
            }}
          }}
        }}
      }}
    }}
    """
    try:
        children_result = g.graphql_api(query_children)
        children_nodes = children_result.get('node', {}).get('children', {}).get('nodes', [])
        return children_nodes
    except Exception as e:
        print(f"::warning::GraphQL query for children of issue #{parent_issue.number} failed: {e}. Skipping.")
        return []


def update_parent_issue(parent_issue, child_issue_number):
    """
    Adds a comment to the parent issue to update its updated_at field.
    """
    print(f"Updating parent issue #{parent_issue.number} with a comment...")
    try:
        comment_body = f"{UPDATE_MESSAGE} Child issue: #{child_issue_number}."
        parent_issue.create_comment(comment_body)
        print(f"Successfully commented on issue #{parent_issue.number}.")
    except Exception as e:
        print(f"::error::Failed to update issue #{parent_issue.number}: {e}")

def main():
    """
    Main function to run the grooming process.
    """
    print("Starting Heirloom Issue Groomer...")
    parent_issues = get_parent_issues()
    
    found_issues = False
    for parent_issue in parent_issues:
        found_issues = True
        print(f"Processing parent issue: #{parent_issue.number} - '{parent_issue.title}'")
        
        children = get_child_issues(parent_issue)
        
        if not children:
            print(f"No sub-issues found for #{parent_issue.number}. Skipping.")
            continue
            
        most_recent_child_update = None
        most_recent_child_number = None

        for child in children:
            child_updated_at = datetime.datetime.fromisoformat(child['updatedAt'].replace('Z', '+00:00'))
            if most_recent_child_update is None or child_updated_at > most_recent_child_update:
                most_recent_child_update = child_updated_at
                most_recent_child_number = child['number']

        if most_recent_child_update:
            days_since_last_child_activity = (datetime.datetime.now(datetime.timezone.utc) - most_recent_child_update).days
            print(f"Most recent child activity on #{most_recent_child_number} was {days_since_last_child_activity} days ago.")

            if days_since_last_child_activity <= DAYS_THRESHOLD:
                # Check if the parent issue is already up-to-date
                parent_updated_at = parent_issue.updated_at.replace(tzinfo=datetime.timezone.utc)
                if most_recent_child_update > parent_updated_at:
                    print(f"Parent issue is older than child. Updating parent issue #{parent_issue.number}...")
                    update_parent_issue(parent_issue, most_recent_child_number)
                else:
                    print(f"Parent issue is already up-to-date. No action needed.")
            else:
                print(f"Child activity is older than threshold of {DAYS_THRESHOLD} days. No action needed.")
        else:
            print(f"No recent activity on sub-issues for #{parent_issue.number}.")

    if not found_issues:
      print("No parent issues found with the specified labels. No action needed.")

if __name__ == "__main__":
    main()
