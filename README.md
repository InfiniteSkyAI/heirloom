# Heirloom

A GitHub Action that prevents parent issues from being marked as stale by checking for recent activity on their sub-issues.

## How it Works

The official `actions/stale` bot is great for managing a backlog, but it doesn't account for the new sub-issue hierarchy in
GitHub Issues. An "epic" issue, for example, might not have direct activity for months, even if its child issues are being
actively worked on. This can lead to important parent issues being incorrectly labeled as stale or closed.

Heirloom solves this by traversing the issue hierarchy. It finds all parent issues with a specific label (e.g., `epic`),
checks all of their child issues for recent activity, and if it finds any, it adds a comment to the parent issue. This
update resets the parent issue's stale timer, effectively keeping it "fresh" as long as there is progress on its sub-tasks.

An optional and more powerful mode, **Heirloom** can also be configured to follow the entire hierarchy automatically. When
enabled, it finds all recently active issues and updates every one of their ancestors in the hierarchy. This ensures all
your high-level plans stay fresh as long as a single sub-task is being worked on.

## Usage

To use this action, you'll create a workflow file in your repository. This action requires a GitHub Personal Access Token
(PAT) with `read:org` and repo scopes.

```yml
# .github/workflows/heirloom.yml

name: 'Heirloom Issue Grooming'

on:
  schedule:
    - cron: '30 1 * * *' # Runs at 1:30 AM every day

jobs:
  grooming:
    runs-on: ubuntu-latest
    steps:
      - name: Heirloom
        uses: InfiniteSkyAI/heirloom@v1
        with:
          github-token: ${{ secrets.PAT_TOKEN }}
          parent-issue-labels: 'epic,keep-open'
          days-threshold: '45'
          update-message: 'Heirloom bot is keeping this epic fresh due to recent child issue activity.'
```

### Inputs
| Name | Description | Required | Default |
|------|-------------|----------|---------|
| `github-token` | Required. A GitHub Personal Access Token (PAT) with appropriate scopes. | `true` ||
| `repo-owner` | The owner of the repository. | false | The repository owner from the GitHub context. |
| `repo-name` | The name of the repository. | false | The repository name from the GitHub context. |
| `parent-issue-labels` | A comma-separated list of labels to identify parent issues (e.g., `epic,story`). | false | `epic,story` |
| `parent-issue-types` | A comma-separated list of issue types to identify parent issues (e.g., `epic,story`). | false | `epic,story` |
| `update-all-ancestors` | When `true`, Heirloom will find all active issues and update all of their ancestors in the hierarchy. This overrides `parent-issue-labels` and `parent-issue-types`. | `false` ||
| `days-threshold` | The number of days of inactivity on a child issue to consider it "stale." If a child issue has been active more recently than this, the parent's timer is reset. | `false` | 30 |
| `update-message` | The comment to add to parent issues to reset their stale timer. | false | "🤖 This parent issue has been updated due to recent activity on a child issue." |

