# Git Work Time Tracker

A Python script that calculates work hours from git commit history using the GitHub API.

## Features

- **GitHub API Integration**: Fetch commits from all branches without cloning the repository
- **OAuth Authentication**: Secure device flow authentication or personal access token support
- **Configurable Parameters**: Customize streak thresholds and work time assumptions
- **Smart Streak Detection**: Automatically merges overlapping work sessions across branches
- **Date Filtering**: Analyze specific time periods using ISO 8601 format
- **User Filtering**: Track work hours for specific contributors
- **JSON Caching**: Save and reload results for quick access
- **Detailed Reporting**: Daily breakdown and total work hours summary

## Installation

1. Clone or download this repository
2. Install dependencies:

```bash
pip install -r requirements.txt
```

## Usage

### Basic Usage

Analyze a repository with default settings (user: mamrehn):

```bash
python git_work_tracker.py --repo https://github.com/owner/repo
```

### With Personal Access Token

Skip OAuth by providing a GitHub personal access token:

```bash
python git_work_tracker.py --repo owner/repo --token YOUR_GITHUB_TOKEN
```

### Filter by Date Range

Analyze commits within a specific time period:

```bash
python git_work_tracker.py --repo owner/repo \
  --start 2026-01-01T00:00:00+01:00 \
  --end 2026-01-23T23:59:59+01:00
```

### Filter by User

Track work hours for a specific contributor:

```bash
python git_work_tracker.py --repo owner/repo --user johndoe
```

### Custom Configuration

Adjust streak detection and work time assumptions:

```bash
python git_work_tracker.py --repo owner/repo \
  --streak-threshold 120 \
  --pre-streak-minutes 30 \
  --post-streak-minutes 10
```

### Load Cached Results

Quickly display previously calculated results:

```bash
python git_work_tracker.py --load work_time_results.json
```

## Configuration Options

| Option | Default | Description |
|--------|---------|-------------|
| `--repo` | - | GitHub repository URL or owner/repo format (required) |
| `--token` | - | GitHub personal access token (alternative to OAuth) |
| `--user` | mamrehn | Git username to filter commits |
| `--start` | - | Start date/time in ISO 8601 format |
| `--end` | - | End date/time in ISO 8601 format |
| `--output` | work_time_results.json | Output JSON file path |
| `--load` | - | Load cached results from JSON file |
| `--streak-threshold` | 90 | Minutes between commits to consider same work session |
| `--pre-streak-minutes` | 20 | Minutes to add before first commit in a streak |
| `--post-streak-minutes` | 5 | Minutes to add after last commit in a streak |

## How It Works

### Work Streak Detection

The script analyzes commit timestamps to identify work sessions (streaks):

1. **Fetch Commits**: Retrieves all commits from all branches for the specified user
2. **Sort by Time**: Orders commits chronologically across all branches
3. **Detect Streaks**: Groups commits within the threshold (default: 90 minutes) into work sessions
4. **Merge Overlaps**: Automatically handles branch switches during the same work session
5. **Calculate Time**: 
   - Adds pre-streak time (default: 20 minutes) before the first commit
   - Includes time between commits in the same streak
   - Adds post-streak time (default: 5 minutes) after the last commit

### Example

Given these commits:
- 09:00 - Commit A (branch: main)
- 09:30 - Commit B (branch: feature)
- 10:00 - Commit C (branch: main)
- 12:00 - Commit D (branch: main)

The script identifies:
- **Streak 1**: 09:00-10:00 (1 hour) + 20 min pre + 5 min post = **1h 25m**
- **Streak 2**: 12:00 (0 hours) + 20 min pre + 5 min post = **25m**
- **Total**: **1h 50m**

## OAuth Setup

When running without a `--token`, the script uses GitHub's device flow OAuth:

1. The script displays a URL and user code
2. Visit the URL in your browser
3. Enter the code to authorize the application
4. The script automatically continues once authorized

No client secret is required for device flow authentication.

## JSON Output Format

The output JSON contains:

```json
{
  "metadata": {
    "repository": "owner/repo",
    "user": "mamrehn",
    "start_date": "2026-01-01T00:00:00+01:00",
    "end_date": null,
    "generated_at": "2026-01-23T07:19:18+01:00",
    "config": {
      "streak_threshold_minutes": 90,
      "pre_streak_minutes": 20,
      "post_streak_minutes": 5
    }
  },
  "commits": [...],
  "work_time": {
    "daily_work": {
      "2026-01-15": {
        "duration_hours": 4.5,
        "duration_minutes": 270,
        "streaks": [...]
      }
    },
    "total_hours": 42.5,
    "total_minutes": 2550
  }
}
```

## API Rate Limits

GitHub API rate limits:
- **Unauthenticated**: 60 requests/hour
- **Authenticated** (OAuth or token): 5,000 requests/hour

The script typically uses one request per branch plus pagination requests for large commit histories.

## License

MIT License - feel free to use and modify as needed.
