#!/usr/bin/env bash
# scripts/status.sh — derive project status from GitHub milestones, PRs, and issues.
#
# Usage:
#   bash scripts/status.sh              # auto-detect repo from the local git remote
#   REPO=owner/name bash scripts/status.sh   # explicit repo (skips git lookup)
#
# Prints a per-phase snapshot pulled live from the GitHub API. No local state —
# the source of truth is whatever GitHub says right now. Replaces hand-maintained
# "definition of done" checkboxes in a roadmap doc.
#
# Convention-agnostic on milestone NAMING: it lists every milestone in order
# (by milestone number — creation order), printing each title verbatim — so it works
# whether you name phases "Phase N — …", "M6 — …", or anything else. It only
# assumes:
# - one milestone per phase;
# - phase tracker issues carry the `phase-tracker` label (shown inline);
# - deferred/cross-phase work carries `follow-up` (and friends), and unmilestoned
#   open work is surfaced as leakage at the end.
#
# Requires a modern `gh` CLI (built-in --jq + --paginate); jq is NOT required.
# With REPO unset, run from inside the repo working tree so `gh repo view` can
# read the remote. `gh api` will prompt unless this exact script invocation
# (`Bash(bash scripts/status.sh)`) is allowlisted — which is the intended setup.
#
# Markers:
#   ✓ complete    (milestone closed, or all items closed)
#   ◐ in progress (some closed, some open)
#   ○ queued      (open items, none closed yet)
#   · empty       (no items milestoned yet)

set -euo pipefail

REPO="${REPO:-$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null || true)}"
if [ -z "$REPO" ]; then
  echo "status.sh: couldn't determine the repo — run inside its git worktree, or set REPO=owner/name." >&2
  exit 1
fi

echo "## Status snapshot — $(date '+%Y-%m-%d %H:%M %Z')"
echo "Repo: $REPO"
echo "Legend: ✓ done   ◐ in progress   ○ queued   · empty"
echo

gh api --paginate "repos/$REPO/milestones?state=all&per_page=100" \
  --jq 'sort_by(.number)[] | [.number, .state, .open_issues, .closed_issues, .title] | @tsv' \
  | while IFS=$'\t' read -r num state open closed title; do
      total=$((open + closed))
      if [ "$state" = "closed" ]; then
        marker="✓"
        bar="$closed/$total closed (milestone closed)"
      elif [ "$total" -eq 0 ]; then
        marker="·"
        bar="empty"
      else
        pct=$((closed * 100 / total))
        bar="$closed/$total closed (${pct}%)"
        if [ "$open" -eq 0 ]; then
          marker="✓"
        elif [ "$closed" -eq 0 ]; then
          marker="○"
        else
          marker="◐"
        fi
      fi
      echo "### $marker $title — $bar"

      gh api --paginate "repos/$REPO/issues?milestone=$num&state=all&per_page=100" \
        --jq '.[] | "  - [\(.state)] \(if .pull_request then "PR " else "Issue " end)#\(.number) — \(.title)\(if (.labels | length) > 0 then "  " + (.labels | map("[" + .name + "]") | join(" ")) else "" end)"'
      echo
    done

# Leakage check: open work outside the milestone structure.
leakage="$(gh api --paginate "repos/$REPO/issues?state=open&per_page=100" \
  --jq '.[] | select(.milestone == null) | "  - \(if .pull_request then "PR " else "Issue " end)#\(.number) — \(.title)"')"

if [ -n "$leakage" ]; then
  count="$(printf '%s\n' "$leakage" | wc -l | tr -d ' ')"
  echo "### ⚠ Open work with no milestone ($count)"
  printf '%s\n' "$leakage"
  echo
  echo "These items aren't slotted into any phase. Either assign a milestone or close them."
fi
