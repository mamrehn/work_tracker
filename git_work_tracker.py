#!/usr/bin/env python3
"""
Git Work Time Tracker

Calculates work hours from git commit history using GitHub API.
Supports OAuth authentication, configurable streak detection, and JSON caching.
"""

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from collections import defaultdict

import requests
from dateutil import parser as date_parser


# Default configuration
DEFAULT_CONFIG = {
    "streak_threshold_minutes": 90,
    "pre_streak_minutes": 20,
    "post_streak_minutes": 5
}


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


def main():
    parser = argparse.ArgumentParser(
        description="Calculate work hours from git commit history using GitHub API"
    )
    parser.add_argument("--repo", help="GitHub repository URL or owner/repo")
    parser.add_argument("--token", help="GitHub personal access token (recommended for higher rate limits)")
    parser.add_argument("--user", help="Git username to filter commits (default: count all users)")
    parser.add_argument("--start", help="Start date/time in ISO 8601 format (e.g., 2026-01-01T00:00:00+01:00)")
    parser.add_argument("--end", help="End date/time in ISO 8601 format")
    parser.add_argument("--output", help="Output JSON file path (default: work_time_results.json)")
    parser.add_argument("--load", help="Load and display cached results from JSON file")
    parser.add_argument("--streak-threshold", type=int, default=DEFAULT_CONFIG["streak_threshold_minutes"],
                       help=f"Minutes between commits to consider same streak (default: {DEFAULT_CONFIG['streak_threshold_minutes']})")
    parser.add_argument("--pre-streak-minutes", type=int, default=DEFAULT_CONFIG["pre_streak_minutes"],
                       help=f"Minutes to add before first commit in streak (default: {DEFAULT_CONFIG['pre_streak_minutes']})")
    parser.add_argument("--post-streak-minutes", type=int, default=DEFAULT_CONFIG["post_streak_minutes"],
                       help=f"Minutes to add after last commit in streak (default: {DEFAULT_CONFIG['post_streak_minutes']})")
    
    args = parser.parse_args()
    
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
        print("Error: --repo is required (unless using --load)", file=sys.stderr)
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
    output_file = args.output or "work_time_results.json"
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
