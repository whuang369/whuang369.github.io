#!/bin/sh
# Install (or reinstall) a LaunchAgent that runs update-token-usage.sh daily at
# 23:50 and at login. To remove it:
#   launchctl bootout gui/$(id -u)/local.token-usage
#   rm ~/Library/LaunchAgents/local.token-usage.plist
set -eu

LABEL=local.token-usage
CLONE="$HOME/Library/Application Support/token-usage/site"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/token-usage.log"

if [ ! -d "$CLONE/.git" ]; then
    mkdir -p "$(dirname "$CLONE")"
    git clone --quiet --branch master "$(git -C "$(dirname "$0")" remote get-url origin)" "$CLONE"
fi

mkdir -p "$(dirname "$PLIST")"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/sh</string>
        <string>$CLONE/scripts/update-token-usage.sh</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>23</integer>
        <key>Minute</key>
        <integer>50</integer>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$LOG</string>
    <key>StandardErrorPath</key>
    <string>$LOG</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed $LABEL: runs daily at 23:50 and at login; log in $LOG"
