#!/usr/bin/env python3
"""
Git Work Time Tracker

Calculates work hours from git commit history using GitHub API.
Supports OAuth authentication, configurable streak detection, and JSON caching.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from collections import defaultdict

import requests
from dateutil import parser as date_parser


# Default configuration
DEFAULT_CONFIG = {
    "streak_threshold_minutes": 60,
    "pre_streak_minutes": 20,
    "post_streak_minutes": 5
}

DEFAULT_OUTPUT_FILE = "efforts.json"


class GitHubAuth:
    """Handles GitHub authentication via personal access token."""

    def __init__(self, token: Optional[str] = None):
        self.token = token
        self.session = requests.Session()

    def get_headers(self) -> Dict[str, str]:
        """Get headers for GitHub API requests."""
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "git-work-tracker"
        }
        if self.token:
            headers["Authorization"] = f"token {self.token}"
        return headers


def is_git_repository(path: str) -> bool:
    """Check if a directory is a git repository."""
    git_dir = os.path.join(path, ".git")
    return os.path.isdir(git_dir)


def get_remote_url(repo_path: str) -> Optional[str]:
    """Get the remote origin URL of a git repository."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=repo_path,
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def is_local_up_to_date(repo_path: str) -> bool:
    """
    Check if the local branch is up-to-date with its remote tracking branch.
    Does NOT fetch or modify the filesystem.
    Returns True if up-to-date (can use local), False otherwise.
    """
    try:
        # Get current branch name
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            return False
        current_branch = result.stdout.strip()

        # Get local commit hash
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            return False
        local_hash = result.stdout.strip()

        # Get remote tracking branch commit hash
        result = subprocess.run(
            ["git", "rev-parse", f"origin/{current_branch}"],
            cwd=repo_path,
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            # No remote tracking branch, can't compare
            return False
        remote_hash = result.stdout.strip()

        # Check if local is at or ahead of remote
        # We consider it "up to date" if the remote hash is an ancestor of local
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", remote_hash, local_hash],
            cwd=repo_path,
            capture_output=True,
            text=True
        )
        return result.returncode == 0
    except Exception:
        return False


def fetch_commits_from_local_git(repo_path: str,
                                  start_date: Optional[datetime] = None,
                                  end_date: Optional[datetime] = None) -> List[Dict]:
    """
    Fetch commits from local git history using git log.
    Returns commits in the same format as GitHub API fetching.
    """
    commits = []
    commit_shas = set()

    # Get all branches
    try:
        result = subprocess.run(
            ["git", "branch", "-a", "--format=%(refname:short)"],
            cwd=repo_path,
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            return []
        branches = [b.strip() for b in result.stdout.strip().split("\n") if b.strip()]
    except Exception:
        return []

    # Filter to local branches only (exclude remotes/origin/...)
    local_branches = [b for b in branches if not b.startswith("remotes/")]

    for branch in local_branches:
        # Build git log command
        log_format = "%H%x00%an%x00%ae%x00%aI%x00%s"  # hash, author, email, date ISO, subject
        cmd = ["git", "log", branch, f"--format={log_format}"]

        if start_date:
            cmd.append(f"--since={start_date.isoformat()}")
        if end_date:
            cmd.append(f"--until={end_date.isoformat()}")

        try:
            result = subprocess.run(
                cmd,
                cwd=repo_path,
                capture_output=True,
                text=True
            )
            if result.returncode != 0:
                continue

            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                parts = line.split("\x00")
                if len(parts) < 5:
                    continue

                sha, author, email, date_str, message = parts[:5]

                if sha not in commit_shas:
                    commit_shas.add(sha)
                    commits.append({
                        "sha": sha,
                        "author": author,
                        "email": email,
                        "date": date_parser.parse(date_str),
                        "message": message,
                        "branch": branch
                    })
        except Exception:
            continue

    return commits


class WorkTimeTracker:
    """Calculates work time from git commits."""
    
    def __init__(self, config: Dict):
        self.config = config
        self.streak_threshold = timedelta(minutes=config["streak_threshold_minutes"])
        self.pre_streak_time = timedelta(minutes=config["pre_streak_minutes"])
        self.post_streak_time = timedelta(minutes=config["post_streak_minutes"])
    
    def parse_repo_url(self, repo_url: str) -> Tuple[str, str]:
        """Parse GitHub repository URL to extract owner and repo name."""
        # Handle various URL formats
        repo_url = repo_url.rstrip("/")
        if repo_url.startswith("https://github.com/"):
            parts = repo_url.replace("https://github.com/", "").split("/")
        elif repo_url.startswith("git@github.com:"):
            parts = repo_url.replace("git@github.com:", "").replace(".git", "").split("/")
        else:
            # Assume format: owner/repo
            parts = repo_url.split("/")
        
        if len(parts) < 2:
            raise ValueError(f"Invalid repository URL: {repo_url}")
        
        return parts[0], parts[1].replace(".git", "")
    
    def fetch_all_commits(self, auth: GitHubAuth, repo_url: str, 
                         username: Optional[str] = None,
                         start_date: Optional[datetime] = None,
                         end_date: Optional[datetime] = None) -> List[Dict]:
        """Fetch all commits from all branches for a specific user."""
        owner, repo = self.parse_repo_url(repo_url)
        headers = auth.get_headers()
        
        print(f"Fetching commits from {owner}/{repo}...")
        
        # Get all branches
        branches_url = f"https://api.github.com/repos/{owner}/{repo}/branches"
        response = requests.get(branches_url, headers=headers)
        response.raise_for_status()
        branches = response.json()
        
        print(f"Found {len(branches)} branches")
        
        all_commits = []
        commit_shas = set()  # To avoid duplicates
        
        for branch in branches:
            branch_name = branch["name"]
            print(f"Fetching commits from branch: {branch_name}", end="\r")
            
            # Fetch commits for this branch
            commits_url = f"https://api.github.com/repos/{owner}/{repo}/commits"
            params = {"sha": branch_name, "per_page": 100}
            
            if username:
                params["author"] = username
            if start_date:
                params["since"] = start_date.isoformat()
            if end_date:
                params["until"] = end_date.isoformat()
            
            page = 1
            while True:
                params["page"] = page
                response = requests.get(commits_url, headers=headers, params=params)
                response.raise_for_status()
                commits = response.json()
                
                if not commits:
                    break
                
                for commit in commits:
                    sha = commit["sha"]
                    if sha not in commit_shas:
                        commit_shas.add(sha)
                        
                        # Parse commit data
                        commit_data = {
                            "sha": sha,
                            "author": commit["commit"]["author"]["name"],
                            "email": commit["commit"]["author"]["email"],
                            "date": date_parser.parse(commit["commit"]["author"]["date"]),
                            "message": commit["commit"]["message"].split("\n")[0],
                            "branch": branch_name
                        }
                        all_commits.append(commit_data)
                
                # Check if there are more pages
                if len(commits) < 100:
                    break
                page += 1
        
        print(f"\nFetched {len(all_commits)} unique commits")
        return all_commits
    
    def calculate_work_streaks(self, commits: List[Dict]) -> List[Dict]:
        """Calculate work streaks from commits."""
        if not commits:
            return []
        
        # Sort commits by date
        sorted_commits = sorted(commits, key=lambda c: c["date"])
        
        streaks = []
        current_streak = {
            "start": sorted_commits[0]["date"],
            "end": sorted_commits[0]["date"],
            "commits": [sorted_commits[0]]
        }
        
        for commit in sorted_commits[1:]:
            time_since_last = commit["date"] - current_streak["end"]
            
            if time_since_last <= self.streak_threshold:
                # Continue current streak
                current_streak["end"] = commit["date"]
                current_streak["commits"].append(commit)
            else:
                # End current streak and start new one
                streaks.append(current_streak)
                current_streak = {
                    "start": commit["date"],
                    "end": commit["date"],
                    "commits": [commit]
                }
        
        # Add the last streak
        streaks.append(current_streak)
        
        return streaks
    
    def calculate_work_time(self, streaks: List[Dict]) -> Dict:
        """Calculate total work time from streaks."""
        daily_work = defaultdict(lambda: {"duration": timedelta(), "streaks": []})
        total_work = timedelta()
        
        for streak in streaks:
            # Calculate streak duration
            streak_duration = (streak["end"] - streak["start"]) + self.pre_streak_time + self.post_streak_time
            
            # Get the date (in the commit's timezone)
            work_date = streak["start"].date()
            
            daily_work[work_date]["duration"] += streak_duration
            daily_work[work_date]["streaks"].append({
                "start": streak["start"].isoformat(),
                "end": streak["end"].isoformat(),
                "commits": len(streak["commits"]),
                "duration_minutes": streak_duration.total_seconds() / 60,
                "authors": list(set(c["author"] for c in streak["commits"]))
            })
            
            total_work += streak_duration
        
        return {
            "daily_work": {str(date): {
                "duration_hours": data["duration"].total_seconds() / 3600,
                "duration_minutes": data["duration"].total_seconds() / 60,
                "streaks": data["streaks"]
            } for date, data in sorted(daily_work.items())},
            "total_hours": total_work.total_seconds() / 3600,
            "total_minutes": total_work.total_seconds() / 60
        }
    
    def generate_summary(self, work_data: Dict, commits: List[Dict]) -> str:
        """Generate a human-readable summary."""
        lines = []
        lines.append("=" * 60)
        lines.append("GIT WORK TIME TRACKER - SUMMARY")
        lines.append("=" * 60)
        lines.append("")
        
        if commits:
            lines.append(f"Total commits analyzed: {len(commits)}")
            lines.append(f"Date range: {min(c['date'] for c in commits).date()} to {max(c['date'] for c in commits).date()}")
            lines.append("")
        
        lines.append(f"TOTAL WORK TIME: {int(work_data['total_hours'])}h {int(work_data['total_minutes'] % 60)}m")
        lines.append("")
        lines.append("DAILY BREAKDOWN:")
        lines.append("-" * 60)
        
        for date, data in work_data["daily_work"].items():
            hours = int(data["duration_hours"])
            minutes = int(data["duration_minutes"] % 60)
            streak_count = len(data["streaks"])
            
            # Aggregate authors for the day
            day_authors = set()
            for streak in data["streaks"]:
                day_authors.update(streak["authors"])
            authors_str = f" [Authors: {', '.join(sorted(day_authors))}]" if day_authors else ""
            
            lines.append(f"{date}: {hours}h {minutes}m ({streak_count} work session{'s' if streak_count != 1 else ''}){authors_str}")
        
        lines.append("=" * 60)
        return "\n".join(lines)


def scan_folder_for_efforts(parent_folder: str,
                            token: Optional[str] = None,
                            start_date: Optional[datetime] = None,
                            end_date: Optional[datetime] = None,
                            config: Optional[Dict] = None) -> Dict:
    """
    Scan a parent folder for git repositories and compute effort for each.

    Args:
        parent_folder: Path to folder containing git repository subfolders
        token: GitHub personal access token (for remote API fallback)
        start_date: Optional start date filter
        end_date: Optional end date filter
        config: Work time tracker configuration

    Returns:
        Dictionary with effort data for each repository
    """
    if config is None:
        config = DEFAULT_CONFIG.copy()

    parent_path = os.path.abspath(parent_folder)
    if not os.path.isdir(parent_path):
        raise ValueError(f"Parent folder does not exist: {parent_path}")

    auth = GitHubAuth(token=token)
    tracker = WorkTimeTracker(config)
    efforts = {}

    # Find all subdirectories that are git repositories
    subdirs = [d for d in os.listdir(parent_path)
               if os.path.isdir(os.path.join(parent_path, d))]

    for subdir in subdirs:
        repo_path = os.path.join(parent_path, subdir)

        if not is_git_repository(repo_path):
            print(f"Skipping {subdir}: not a git repository")
            continue

        print(f"\nProcessing repository: {subdir}")

        commits = []
        source = "unknown"
        remote_url = get_remote_url(repo_path)

        # Check if local is up-to-date with remote
        if is_local_up_to_date(repo_path):
            print(f"  Local branch is up-to-date, using local git history...")
            commits = fetch_commits_from_local_git(repo_path, start_date, end_date)
            source = "local"
        else:
            # Try to use remote API
            if remote_url and "github.com" in remote_url:
                print(f"  Local not up-to-date, using GitHub API...")
                try:
                    commits = tracker.fetch_all_commits(
                        auth, remote_url,
                        username=None,  # All users
                        start_date=start_date,
                        end_date=end_date
                    )
                    source = "github_api"
                except Exception as e:
                    print(f"  Warning: Failed to fetch from GitHub API: {e}")
                    print(f"  Falling back to local git history...")
                    commits = fetch_commits_from_local_git(repo_path, start_date, end_date)
                    source = "local_fallback"
            else:
                print(f"  No GitHub remote found, using local git history...")
                commits = fetch_commits_from_local_git(repo_path, start_date, end_date)
                source = "local"

        if not commits:
            print(f"  No commits found")
            efforts[subdir] = {
                "total_hours": 0,
                "total_minutes": 0,
                "total_commits": 0,
                "source": source,
                "remote_url": remote_url,
                "users": {}
            }
            continue

        print(f"  Found {len(commits)} commits")

        # Group commits by author
        commits_by_author = defaultdict(list)
        for commit in commits:
            commits_by_author[commit["author"]].append(commit)

        # Calculate work time for each author
        users_data = {}
        for author, author_commits in commits_by_author.items():
            streaks = tracker.calculate_work_streaks(author_commits)
            work_data = tracker.calculate_work_time(streaks)
            users_data[author] = {
                "total_hours": work_data["total_hours"],
                "total_minutes": work_data["total_minutes"],
                "total_commits": len(author_commits),
                "daily_work": work_data["daily_work"]
            }

        # Calculate totals across all authors
        total_hours = sum(u["total_hours"] for u in users_data.values())
        total_minutes = sum(u["total_minutes"] for u in users_data.values())
        total_commits = sum(u["total_commits"] for u in users_data.values())

        # Date range
        all_dates = [c["date"] for c in commits]
        date_range = {
            "start": min(all_dates).isoformat() if all_dates else None,
            "end": max(all_dates).isoformat() if all_dates else None
        }

        efforts[subdir] = {
            "total_hours": total_hours,
            "total_minutes": total_minutes,
            "total_commits": total_commits,
            "date_range": date_range,
            "source": source,
            "remote_url": remote_url,
            "users": users_data
        }

        print(f"  Total: {int(total_hours)}h {int(total_minutes % 60)}m across {len(users_data)} user(s)")

    return efforts


def save_efforts_to_json(efforts: Dict, output_path: str = DEFAULT_OUTPUT_FILE):
    """
    Save efforts data to JSON file, merging with existing data.

    Args:
        efforts: Dictionary with effort data for each repository
        output_path: Path to output JSON file
    """
    existing_data = {}

    # Load existing file if it exists
    if os.path.exists(output_path):
        try:
            with open(output_path, 'r') as f:
                existing_data = json.load(f)
        except (json.JSONDecodeError, IOError):
            existing_data = {}

    # Merge: override existing repos with new data
    existing_data.update(efforts)

    # Add metadata
    result = {
        "metadata": {
            "generated_at": datetime.now().isoformat(),
            "total_repositories": len(existing_data) - 1 if "metadata" in existing_data else len(existing_data)
        },
        "repositories": {k: v for k, v in existing_data.items() if k not in ("metadata", "repositories")}
    }

    # If existing had repositories key, merge those too
    if "repositories" in existing_data:
        result["repositories"].update(existing_data["repositories"])
        result["repositories"].update(efforts)

    result["metadata"]["total_repositories"] = len(result["repositories"])

    with open(output_path, 'w') as f:
        json.dump(result, f, indent=2)

    print(f"\n✓ Results saved to: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Calculate work hours from git commit history using GitHub API"
    )
    parser.add_argument("--repo", help="GitHub repository URL or owner/repo")
    parser.add_argument("--scan-folder", help="Scan a folder containing multiple git repositories and compute efforts for each")
    parser.add_argument("--token", help="GitHub personal access token (recommended for higher rate limits)")
    parser.add_argument("--user", help="Git username to filter commits (default: count all users)")
    parser.add_argument("--start", help="Start date/time in ISO 8601 format (e.g., 2026-01-01T00:00:00+01:00)")
    parser.add_argument("--end", help="End date/time in ISO 8601 format")
    parser.add_argument("--output", help=f"Output JSON file path (default: {DEFAULT_OUTPUT_FILE})")
    parser.add_argument("--load", help="Load and display cached results from JSON file")
    parser.add_argument("--streak-threshold", type=int, default=DEFAULT_CONFIG["streak_threshold_minutes"],
                       help=f"Minutes between commits to consider same streak (default: {DEFAULT_CONFIG['streak_threshold_minutes']})")
    parser.add_argument("--pre-streak-minutes", type=int, default=DEFAULT_CONFIG["pre_streak_minutes"],
                       help=f"Minutes to add before first commit in streak (default: {DEFAULT_CONFIG['pre_streak_minutes']})")
    parser.add_argument("--post-streak-minutes", type=int, default=DEFAULT_CONFIG["post_streak_minutes"],
                       help=f"Minutes to add after last commit in streak (default: {DEFAULT_CONFIG['post_streak_minutes']})")

    args = parser.parse_args()

    # Handle --scan-folder mode
    if args.scan_folder:
        # Parse dates if provided
        start_date = date_parser.parse(args.start) if args.start else None
        end_date = date_parser.parse(args.end) if args.end else None

        # Setup configuration
        config = {
            "streak_threshold_minutes": args.streak_threshold,
            "pre_streak_minutes": args.pre_streak_minutes,
            "post_streak_minutes": args.post_streak_minutes
        }

        print(f"Scanning folder: {args.scan_folder}")
        print("=" * 60)

        try:
            efforts = scan_folder_for_efforts(
                parent_folder=args.scan_folder,
                token=args.token,
                start_date=start_date,
                end_date=end_date,
                config=config
            )
        except Exception as e:
            print(f"Error scanning folder: {e}", file=sys.stderr)
            sys.exit(1)

        # Save results
        output_file = args.output or DEFAULT_OUTPUT_FILE
        save_efforts_to_json(efforts, output_file)

        # Print summary (sorted by effort spent, descending)
        print("\n" + "=" * 60)
        print("SCAN COMPLETE - SUMMARY (sorted by effort)")
        print("=" * 60)
        total_hours_all = 0
        sorted_efforts = sorted(efforts.items(), key=lambda x: x[1]["total_hours"], reverse=True)
        for repo_name, repo_data in sorted_efforts:
            hours = repo_data["total_hours"]
            minutes = repo_data["total_minutes"] % 60
            commits = repo_data["total_commits"]
            users = len(repo_data.get("users", {}))
            total_hours_all += repo_data["total_hours"]
            print(f"{repo_name}: {int(hours)}h {int(minutes)}m ({commits} commits, {users} user(s))")
        print("-" * 60)
        print(f"TOTAL ACROSS ALL REPOS: {int(total_hours_all)}h {int((total_hours_all * 60) % 60)}m")
        print("=" * 60)
        return

    # Load cached results if requested
    if args.load:
        try:
            with open(args.load, 'r') as f:
                cached_data = json.load(f)
            
            print("\n" + "=" * 60)
            print("LOADED CACHED RESULTS")
            print("=" * 60)
            print(f"Repository: {cached_data['metadata']['repository']}")
            print(f"User: {cached_data['metadata']['user'] or 'All users'}")
            print(f"Generated: {cached_data['metadata']['generated_at']}")
            print("")
            
            # Fix dates in cached data
            for commit in cached_data['commits']:
                commit['date'] = date_parser.parse(commit['date'])
            
            # Fix dates in streaks
            for date_str, daily_data in cached_data['work_time']['daily_work'].items():
                for streak in daily_data['streaks']:
                    # These are just needed as datetime objects for min/max in generate_summary
                    # though they are currently strings in the JSON streaks
                    pass
            
            # Since generate_summary needs commit['date'] to be datetime objects
            # and it uses them for min/max, we just need to ensure commits have datetime objects
            
            # Generate summary from cached data
            tracker = WorkTimeTracker(cached_data['metadata']['config'])
            summary = tracker.generate_summary(cached_data['work_time'], cached_data['commits'])
            print(summary)
            return
        except Exception as e:
            print(f"Error loading cached results: {e}", file=sys.stderr)
            sys.exit(1)
    
    # Validate required arguments
    if not args.repo:
        print("Error: --repo is required (unless using --load or --scan-folder)", file=sys.stderr)
        parser.print_help()
        sys.exit(1)
    
    # Parse dates if provided
    start_date = date_parser.parse(args.start) if args.start else None
    end_date = date_parser.parse(args.end) if args.end else None
    
    # Setup configuration
    config = {
        "streak_threshold_minutes": args.streak_threshold,
        "pre_streak_minutes": args.pre_streak_minutes,
        "post_streak_minutes": args.post_streak_minutes,
        "default_user": args.user
    }
    
    # Setup authentication
    auth = GitHubAuth(token=args.token)
    if not auth.token:
        print("Warning: No GitHub token provided. API rate limit will be 60 requests/hour.")
        print("For higher rate limits (5000 req/hour), provide a token with --token")
        print("Create a token at: https://github.com/settings/tokens\n")
    
    # Initialize tracker
    tracker = WorkTimeTracker(config)
    
    # Fetch commits
    try:
        commits = tracker.fetch_all_commits(
            auth, args.repo, 
            username=args.user,
            start_date=start_date,
            end_date=end_date
        )
    except Exception as e:
        print(f"Error fetching commits: {e}", file=sys.stderr)
        sys.exit(1)
    
    if not commits:
        print("No commits found matching the criteria.")
        sys.exit(0)
    
    # Calculate work streaks
    streaks = tracker.calculate_work_streaks(commits)
    
    # Calculate work time
    work_data = tracker.calculate_work_time(streaks)
    
    # Generate and print summary
    summary = tracker.generate_summary(work_data, commits)
    print("\n" + summary)
    
    # Save to JSON
    output_file = args.output or DEFAULT_OUTPUT_FILE
    result_data = {
        "metadata": {
            "repository": args.repo,
            "user": args.user,
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": end_date.isoformat() if end_date else None,
            "generated_at": datetime.now().isoformat(),
            "config": config
        },
        "commits": [{
            "sha": c["sha"],
            "author": c["author"],
            "email": c["email"],
            "date": c["date"].isoformat(),
            "message": c["message"],
            "branch": c["branch"]
        } for c in commits],
        "work_time": work_data
    }
    
    with open(output_file, 'w') as f:
        json.dump(result_data, f, indent=2)
    
    print(f"\n✓ Results saved to: {output_file}")


if __name__ == "__main__":
    main()
