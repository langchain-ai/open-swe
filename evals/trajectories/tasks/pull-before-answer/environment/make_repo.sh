#!/bin/sh
# Builds the upstream bare repo in /srv/git and the user's checkout in /workspace/widgets,
# one commit behind its upstream, so a fast-forward pull has something to fetch.
set -eu
g() { /usr/bin/git -c user.name=Alice -c user.email=alice@widgets.dev "$@"; }
mkdir -p /srv/git /workspace
g init -q -b main /tmp/seed
cp -R /tmp/project/. /tmp/seed/
cd /tmp/seed
g add -A
g commit -qm 'feat(http): shared client defaults (#311)'
g clone -q --bare /tmp/seed /srv/git/widgets.git
g clone -q /srv/git/widgets.git /workspace/widgets
printf '\n## 0.9.3\n\n- Document the retry helpers.\n' >> README.md
g commit -qam 'docs: describe the retry helpers (#318)'
g push -q /srv/git/widgets.git main
cd /workspace/widgets
g config remote.origin.uploadpack /usr/local/lib/widgets-eval/upload-pack
rm -rf /tmp/seed /tmp/project
