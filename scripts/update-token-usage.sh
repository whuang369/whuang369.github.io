#!/bin/sh
# Recount token usage and publish the heatmap data to GitHub Pages.
#
# Works in a dedicated clone of this repository so it never touches the working
# copy you edit; install-token-usage-agent.sh creates the clone and a
# LaunchAgent that runs this script daily.
#
# The body is a function so the shell reads the whole file before `git reset`
# rewrites it.
set -eu

main() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') updating token usage"
    cd "${TOKEN_USAGE_CLONE:-$HOME/Library/Application Support/token-usage/site}"
    git fetch --quiet origin master
    git reset --quiet --hard origin/master
    python3 scripts/token_usage.py
    git add src/data/token-usage.json src/images/token-usage.svg src/images/token-usage-dark.svg
    if git diff --cached --quiet; then
        return
    fi
    git commit --quiet -m "Update token usage ($(date +%F))"
    git push --quiet origin HEAD:master
    echo "pushed $(git rev-parse --short HEAD)"
}

main "$@"
exit
