import os
import requests
from datetime import datetime, timedelta
import time
import random
import shutil
import subprocess
import json

GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"
GITHUB_REST_URL = "https://api.github.com/repos"

# Module-level verbose flag (can be toggled by main)
VERBOSE = False


def _get_input(name):
    """Return environment input for `name`, accepting both hyphen and underscore.

    Examples: _get_input('INPUT_REPO-OWNER') will also check INPUT_REPO_OWNER.
    """
    val = os.environ.get(name)
    if val is not None:
        return val
    alt = name.replace('-', '_')
    return os.environ.get(alt)


def request_with_retries(method, url, headers=None, json=None, max_retries=4, backoff_factor=0.5, timeout=10):
    """Perform an HTTP request with retries and exponential backoff.

    method: 'get' or 'post'
    """
    attempt = 0
    while True:
        try:
            if method.lower() == "post":
                resp = requests.post(url, headers=headers, json=json, timeout=timeout)
            else:
                resp = requests.get(url, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp
        except requests.exceptions.RequestException:
            attempt += 1
            if attempt > max_retries:
                raise
            sleep_time = backoff_factor * (2 ** (attempt - 1))
            jitter = random.uniform(0, sleep_time * 0.1)
            time.sleep(sleep_time + jitter)


# Note: parent resolution via ProjectV2 has been removed. If you need parent
# lookup functionality in future, implement it using repository-specific
# links or Issue-level fields.


def add_comment_to_issue(repo_owner, repo_name, issue_id, token, message, days_threshold=None, dry_run=False):
    """Adds a comment to a given issue. If days_threshold is set, skip
    commenting if the issue was updated within that many days.

    When dry_run is True, the function will only print the action instead
    of performing the POST.
    """
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    if days_threshold is not None:
        try:
            issue_url = f"{GITHUB_REST_URL}/{repo_owner}/{repo_name}/issues/{issue_id}"
            r = request_with_retries("get", issue_url, headers=headers)
            issue_data = r.json()
            updated_at_str = issue_data.get("updated_at")
            if updated_at_str:
                try:
                    updated_at = datetime.fromisoformat(updated_at_str.rstrip("Z"))
                except Exception:
                    updated_at = datetime.fromisoformat(updated_at_str)

                if datetime.now() - updated_at < timedelta(days=days_threshold):
                    print(f"Skipping comment on issue #{issue_id}: updated within {days_threshold} days ({updated_at_str})")
                    return
        except requests.exceptions.RequestException as e:
            print(f"Warning: could not fetch issue #{issue_id} to check updated_at: {e}")
            return

    if dry_run:
        print(f"[DRY-RUN] Would add comment to issue #{issue_id}: {message}")
        return

    url = f"{GITHUB_REST_URL}/{repo_owner}/{repo_name}/issues/{issue_id}/comments"
    payload = {"body": message}
    request_with_retries("post", url, headers=headers, json=payload)
    print(f"Successfully added comment to issue #{issue_id}.")


def process_issues_by_hierarchy(repo_owner, repo_name, token, days_threshold, update_message, dry_run=False):
    print("Executing Hierarchical Grooming Mode (scanning stale open issues)...")

    # Query open issues ordered by UPDATED_AT ascending (oldest first). We
    # only need to inspect issues that haven't been updated within the
    # `days_threshold` window; once we reach an issue newer than the cutoff we
    # can stop scanning because all subsequent issues will also be newer.
    query = """
    query($owner: String!, $name: String!, $after: String) {
      repository(owner: $owner, name: $name) {
        issues(first: 100, states: OPEN, orderBy: {field: UPDATED_AT, direction: ASC}, after: $after) {
          nodes { number title updatedAt }
          pageInfo { hasNextPage endCursor }
        }
      }
    }
    """

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    processed_parents = set()
    after_cursor = None

    cutoff = datetime.now() - timedelta(days=days_threshold)

    while True:
        variables = {"owner": repo_owner, "name": repo_name, "after": after_cursor}
        resp = request_with_retries("post", GITHUB_GRAPHQL_URL, headers=headers, json={"query": query, "variables": variables})
        data = resp.json()

        try:
            issues_block = data["data"]["repository"]["issues"]
            nodes = issues_block.get("nodes", [])
        except (KeyError, TypeError):
            if VERBOSE:
                print("GraphQL response while listing issues:", data)
            print("No issues found or could not parse GraphQL response.")
            return

        if not nodes:
            print("No issues found in repository.")
            return

        # If any issue in this page is newer than the cutoff we can stop.
        stop_all = False

        for issue in nodes:
            issue_number = issue.get("number")
            if issue_number is None:
                continue

            updated_at_str = issue.get("updatedAt")
            if updated_at_str:
                try:
                    updated_at = datetime.fromisoformat(updated_at_str.rstrip("Z"))
                except Exception:
                    try:
                        updated_at = datetime.fromisoformat(updated_at_str)
                    except Exception:
                        updated_at = None
                if updated_at and updated_at > cutoff:
                    stop_all = True
                    break

            # Skip if we've already updated this parent in this run
            if issue_number in processed_parents:
                continue

            # Find the most recent activity among descendant (sub) issues
            most_recent_child_activity = get_most_recent_child_activity(repo_owner, repo_name, issue_number, token)
            if most_recent_child_activity:
                age_days = (datetime.now() - most_recent_child_activity).days
                if age_days < days_threshold:
                    print(f"Parent #{issue_number} has a descendant updated {age_days} days ago (within {days_threshold}) — refreshing parent.")
                    try:
                        add_comment_to_issue(repo_owner, repo_name, issue_number, token, update_message, days_threshold, dry_run=dry_run)
                    except requests.exceptions.RequestException as e:
                        print(f"Error adding comment to issue #{issue_number}: {e}")
                    processed_parents.add(issue_number)

            # be polite to the API
            time.sleep(0.2)

        page_info = issues_block.get("pageInfo", {})
        if stop_all:
            print("Completed scanning stale open issues for descendant activity (newer issues skipped).")
            break
        if not page_info.get("hasNextPage"):
            print("Completed scanning all (stale) issues for descendant activity.")
            break
        after_cursor = page_info.get("endCursor")
        time.sleep(0.5)


def process_issues_by_labels(repo_owner, repo_name, token, parent_labels, parent_types, update_message, days_threshold, dry_run=False):
    print("Executing Label/Type-based Grooming Mode...")

    query_filters = []
    if parent_labels:
        quoted = ",".join([f'"{l.strip()}"' for l in parent_labels if l.strip()])
        if quoted:
            query_filters.append(f"labels: [{quoted}]")
    if parent_types:
        types_list = ",".join([t.strip() for t in parent_types if t.strip()])
        if types_list:
            query_filters.append(f"issueTypes: [{types_list}]")

    issues_filter_string = ", ".join(query_filters)
    issues_arg = f", {issues_filter_string}" if issues_filter_string else ""

    query_parent_issues = f"""
    query {{
      repository(owner: "{repo_owner}", name: "{repo_name}") {{
        issues(first: 50{issues_arg}, states: OPEN) {{
          nodes {{ id number title updatedAt }}
        }}
      }}
    }}
    """

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    resp = request_with_retries("post", GITHUB_GRAPHQL_URL, headers=headers, json={"query": query_parent_issues})
    data = resp.json()

    try:
        parent_issues = data["data"]["repository"]["issues"]["nodes"]
    except (KeyError, TypeError) as e:
        if VERBOSE:
            print("GraphQL response while fetching parent issues:", data)
            if isinstance(data, dict) and data.get("errors"):
                print("GraphQL errors:", data.get("errors"))
        print(f"Warning: Could not fetch parent issues: {e}")
        return

    for issue in parent_issues:
        issue_number = issue.get("number")
        print(f"Processing parent issue: #{issue_number} - {issue.get('title')}")

        most_recent_child_activity = get_most_recent_child_activity(repo_owner, repo_name, issue_number, token)
        if most_recent_child_activity:
            current_date = datetime.now()
            days_since_child_activity = (current_date - most_recent_child_activity).days
            if days_since_child_activity < days_threshold:
                try:
                    add_comment_to_issue(repo_owner, repo_name, issue_number, token, update_message, days_threshold, dry_run=dry_run)
                except requests.exceptions.RequestException as e:
                    print(f"Error adding comment to issue #{issue_number}: {e}")
            else:
                print(f"No recent child activity for issue #{issue_number}. It will be handled by the stale bot.")
        else:
            print(f"No sub-issues found or could not determine activity for issue #{issue_number}.")


def get_most_recent_child_activity(repo_owner, repo_name, issue_id, token, debug=False):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # We'll perform a breadth-first traversal over subIssues, batching
    # GraphQL queries for multiple parent issues per request to reduce the
    # number of HTTP calls. We detect cycles using `visited` and enforce a
    # safety cap to avoid runaway traversal.
    batch_size = 10
    max_visit = 2000  # safety cap on number of distinct issues visited

    most_recent_date = None
    details = []
    raw_batches = []

    # Read ignore list of actor logins from env, default to github-actions[bot]
    ignore_actors_env = _get_input("INPUT_IGNORE-ACTORS") or ""
    ignore_actors = set([a.strip() for a in ignore_actors_env.split(',') if a.strip()])
    if not ignore_actors:
        ignore_actors = {"github-actions[bot]"}

    # BFS queue: start from direct children of the initial issue
    to_process = [issue_id]
    visited = set([issue_id])

    try:
        while to_process:
            # Prepare a batch of parents to query
            batch = []
            while to_process and len(batch) < batch_size:
                batch.append(to_process.pop(0))

            if not batch:
                break

            # Build a batched GraphQL query to fetch subIssues for each parent
            fragments = []
            for idx, parent_num in enumerate(batch):
                # Also fetch the last comment (author + updatedAt) so we can
                # detect bot comments (for example actions/stale) and ignore
                # them when deciding whether a sub-issue has seen recent human
                # activity.
                fragments.append(
                    f'i{idx}: repository(owner: "{repo_owner}", name: "{repo_name}") {{ issue(number: {parent_num}) {{ subIssues(first:100) {{ nodes {{ __typename ... on Issue {{ number updatedAt comments(last:1) {{ nodes {{ author {{ login __typename }} updatedAt }} }} }} }} }} }} }}'
                )

            batched_query = "query{\n  " + "\n  ".join(fragments) + "\n}"
            resp = request_with_retries("post", GITHUB_GRAPHQL_URL, headers=headers, json={"query": batched_query})
            data = resp.json()
            raw_batches.append(data)

            repo_data = data.get("data", {}) if isinstance(data, dict) else {}

            # Parse results for each alias
            for idx, parent_num in enumerate(batch):
                alias = f'i{idx}'
                entry = repo_data.get(alias)
                if not entry:
                    # missing data for this parent; skip
                    if VERBOSE:
                        print(f"No GraphQL data for parent {parent_num}:", entry)
                    continue

                try:
                    nodes = entry["issue"]["subIssues"]["nodes"]
                except Exception:
                    if VERBOSE:
                        print(f"Malformed subIssues result for parent {parent_num}:", entry)
                    continue

                for node in nodes:
                    if not node or not isinstance(node, dict):
                        continue
                    if node.get("__typename") != "Issue":
                        continue
                    child_number = node.get("number")
                    updated_at_str = node.get("updatedAt")
                    if child_number is None or not updated_at_str:
                        continue

                    # Inspect last comment author (if present) to see whether
                    # the recent update was caused by a known bot actor. If so,
                    # we treat it as non-human activity for the purpose of
                    # deciding to refresh parents.
                    last_comment_author = None
                    last_comment_type = None
                    last_comment_updated = None
                    try:
                        comments_nodes = node.get("comments", {}).get("nodes", []) or []
                        if comments_nodes:
                            last = comments_nodes[-1]
                            author = last.get("author") or {}
                            last_comment_author = author.get("login")
                            last_comment_type = author.get("__typename")
                            last_comment_updated = last.get("updatedAt")
                    except Exception:
                        # tolerate malformed comment structures
                        last_comment_author = None
                        last_comment_type = None
                        last_comment_updated = None

                    # Parse time
                    try:
                        updated_at = datetime.fromisoformat(updated_at_str.rstrip("Z"))
                    except Exception:
                        try:
                            updated_at = datetime.fromisoformat(updated_at_str)
                        except Exception:
                            if VERBOSE:
                                print(f"Could not parse updatedAt for subIssue {child_number} of parent {parent_num}:", updated_at_str)
                            continue

                    # Decide whether this updatedAt should be considered
                    # 'human' activity. If the last comment was authored by a
                    # configured ignored actor and the comment timestamp
                    # matches (or is after) the issue's updatedAt, we ignore
                    # it.
                    consider_as_activity = True
                    # Detect bot authors heuristically: either the GraphQL
                    # __typename is 'Bot' or the login contains 'bot'. We always
                    # ignore bot authors regardless of INPUT_IGNORE-ACTORS.
                    bot_author = False
                    try:
                        if last_comment_type and str(last_comment_type).lower() == 'bot':
                            bot_author = True
                        elif last_comment_author:
                            la = last_comment_author.lower()
                            # common bot indicators: suffix '[bot]', dependabot
                            # mentions, explicit 'github-actions' login, or any
                            # login containing 'bot'
                            if la.endswith('[bot]') or 'dependabot' in la or la.startswith('github-actions') or 'bot' in la:
                                bot_author = True
                    except Exception:
                        bot_author = False

                    if bot_author:
                        try:
                            if last_comment_updated:
                                lc_dt = datetime.fromisoformat(last_comment_updated.rstrip("Z"))
                                if lc_dt >= updated_at:
                                    consider_as_activity = False
                        except Exception:
                            consider_as_activity = True
                    elif last_comment_author and last_comment_author in ignore_actors:
                        try:
                            if last_comment_updated:
                                lc_dt = datetime.fromisoformat(last_comment_updated.rstrip("Z"))
                                if lc_dt >= updated_at:
                                    consider_as_activity = False
                        except Exception:
                            consider_as_activity = True

                    # Record most recent (only consider human activity)
                    if consider_as_activity:
                        if most_recent_date is None or updated_at > most_recent_date:
                            most_recent_date = updated_at

                    if debug:
                        details.append({
                            "source": "subIssue",
                            "number": child_number,
                            "updated_at": updated_at,
                            "parent": parent_num,
                            "last_comment_author": last_comment_author,
                            "last_comment_updated": last_comment_updated,
                            "consider_as_activity": consider_as_activity,
                        })

                    # Enqueue for further traversal if we haven't seen it before
                    if child_number not in visited:
                        visited.add(child_number)
                        to_process.append(child_number)

                        # Safety cap
                        if len(visited) >= max_visit:
                            if VERBOSE:
                                print(f"Reached max_visit cap ({max_visit}); stopping traversal")
                            to_process = []
                            break

    except Exception as e:
        if VERBOSE:
            print(f"Error during subIssues traversal for issue {issue_id}:", e)
        # best-effort: return whatever we found so far

    if debug:
        return most_recent_date, {
            "details": details,
            "raw_subissues_batches": raw_batches,
        }
    return most_recent_date


def inspect_project_items(repo_owner, repo_name, issue_id, token):
    """Run a direct GraphQL query to fetch `projectItems` nodes for an issue and
    return the raw JSON payload for debugging."""
    # Removed: projectItems inspection was ProjectV2-specific. Keep the
    # function as a placeholder if future repo-specific debugging is needed.
    return {"error": "inspect_project_items removed; use subIssues-based inspection"}


def inspect_node(node_id, token):
        """Removed: node inspection was ProjectV2-specific."""
        return {"error": "inspect_node removed; ProjectV2 not used"}


def main():
    global VERBOSE

    token = _get_input("INPUT_GITHUB-TOKEN")
    repo_owner = _get_input("INPUT_REPO-OWNER")
    repo_name = _get_input("INPUT_REPO-NAME")

    parent_labels_env = _get_input("INPUT_PARENT-ISSUE-LABELS") or ""
    parent_labels = parent_labels_env.split(",") if parent_labels_env else []
    parent_types_env = _get_input("INPUT_PARENT-ISSUE-TYPES") or ""
    parent_types = parent_types_env.split(",") if parent_types_env else []

    days_threshold_env = _get_input("INPUT_DAYS-THRESHOLD")
    try:
        days_threshold = int(days_threshold_env) if days_threshold_env is not None else 30
    except ValueError:
        print(f"Warning: INPUT_DAYS-THRESHOLD value '{days_threshold_env}' is invalid, defaulting to 30")
        days_threshold = 30

    update_message = _get_input("INPUT_UPDATE-MESSAGE") or "Updated by Heirloom"
    update_all_ancestors = (_get_input("INPUT_UPDATE-ALL-ANCESTORS") or "false").lower() == "true"
    dry_run = (_get_input("INPUT_DRY-RUN") or "false").lower() == "true"
    VERBOSE = (_get_input("INPUT_VERBOSE") or "false").lower() == "true"

    # If no token was provided via the environment, try to obtain one from the
    # local GitHub CLI (`gh auth token`). This lets developers run the action
    # locally without looking up a PAT manually (provided `gh` is installed
    # and authenticated).
    if not token:
        gh_path = shutil.which("gh")
        if gh_path:
            try:
                completed = subprocess.run([gh_path, "auth", "token"], capture_output=True, text=True, check=True)
                token_from_gh = completed.stdout.strip()
                if token_from_gh:
                    token = token_from_gh
                    print("Using GitHub token obtained from `gh auth token`.")
            except subprocess.CalledProcessError:
                print("Found 'gh' but could not get a token; please run 'gh auth login' or set INPUT_GITHUB-TOKEN.")
        else:
            print("No INPUT_GITHUB-TOKEN and 'gh' CLI not found. To run locally, install GitHub CLI and run 'gh auth login' or set INPUT_GITHUB-TOKEN in the environment.")

    if not token:
        gh_path = shutil.which("gh")
        if gh_path:
            try:
                completed = subprocess.run([gh_path, "auth", "token"], capture_output=True, text=True, check=True)
                token_from_gh = completed.stdout.strip()
                if token_from_gh:
                    token = token_from_gh
                    if VERBOSE:
                        print("Using GitHub token obtained from `gh auth token`.")
            except subprocess.CalledProcessError:
                if VERBOSE:
                    print("Found 'gh' but could not get a token; please run 'gh auth login' or set INPUT_GITHUB-TOKEN.")
        else:
            if VERBOSE:
                print("No INPUT_GITHUB-TOKEN and 'gh' CLI not found. To run locally, install GitHub CLI and run 'gh auth login' or set INPUT_GITHUB-TOKEN in the environment.")

    if not all([token, repo_owner, repo_name]):
        print("Missing required inputs. Please check your workflow configuration.")
        exit(1)

    print(f"Starting Heirloom bot for {repo_owner}/{repo_name}...")

    # Optional: inspect a single issue and print debug details (bypass full run)
    inspect_issue_env = _get_input("INPUT_INSPECT-ISSUE")
    if inspect_issue_env:
        try:
            inspect_num = int(inspect_issue_env)
        except ValueError:
            print(f"INPUT_INSPECT-ISSUE value '{inspect_issue_env}' is not an integer.")
            exit(1)

        print(f"Inspecting issue #{inspect_num} (debug mode)...")
        # Run the child-activity collector in debug mode to obtain per-descendant info
        most_recent, debug_details = None, None
        try:
            # call with debug=True; function now returns (most_recent_date, details_dict)
            most_recent, debug_info = get_most_recent_child_activity(repo_owner, repo_name, inspect_num, token, debug=True)
        except Exception as e:
            print(f"Error inspecting issue #{inspect_num}: {e}")
            exit(1)

        # For transparency, probe token scopes again so we can report them
        try:
            probe_headers = {"Authorization": f"Bearer {token}"}
            probe_resp = request_with_retries("get", "https://api.github.com/", headers=probe_headers)
            scopes_header_now = probe_resp.headers.get("X-OAuth-Scopes") or probe_resp.headers.get("x-oauth-scopes") or ""
            print(f"Token scopes header: {scopes_header_now}")
        except Exception:
            if VERBOSE:
                print("Could not probe token scopes during inspection.")

        # Print the GraphQL-derived debug details
        if debug_info and isinstance(debug_info, dict):
            dlist = debug_info.get("details", [])
            # Prefer Issue.subIssues output when present
            if "raw_subissues_query" in debug_info:
                if dlist:
                    print(f"Found {len(dlist)} child entries via Issue.subIssues GraphQL:")
                    for ent in dlist:
                        print(" -", ent)
                else:
                    print("No subIssues found via Issue.subIssues GraphQL for this parent.")
                    try:
                        raw = debug_info.get("raw_subissues_query")
                        print("Raw subIssues GraphQL response:")
                        try:
                            print(json.dumps(raw, indent=2))
                        except Exception:
                            print(raw)
                    except Exception as e:
                        print(f"Could not fetch raw subIssues for inspection: {e}")
            else:
                # Generic fallback: no subIssue-specific data present in the
                # debug payload; print whatever details we have.
                if not dlist:
                    print("No descendant entries found for this parent.")
                else:
                    print(f"Found {len(dlist)} descendant entries:")
                    for ent in dlist:
                        print(" -", ent)

        if most_recent:
            age_days = (datetime.now() - most_recent).days
            print(f"Most recent descendant activity: {most_recent.isoformat()} ({age_days} days ago)")
        else:
            print("No descendant activity detected via GraphQL subIssues.")

        # Stop after inspection
        return

    # No token scope probe required — we only use Issue.subIssues GraphQL queries.

    if update_all_ancestors:
        process_issues_by_hierarchy(repo_owner, repo_name, token, days_threshold, update_message, dry_run=dry_run)
    else:
        if not parent_labels and not parent_types:
            print("At least one of 'parent-issue-labels' or 'parent-issue-types' must be provided when not using 'update-all-ancestors'.")
            exit(1)
        process_issues_by_labels(repo_owner, repo_name, token, parent_labels, parent_types, update_message, days_threshold, dry_run=dry_run)


if __name__ == "__main__":
    main()
