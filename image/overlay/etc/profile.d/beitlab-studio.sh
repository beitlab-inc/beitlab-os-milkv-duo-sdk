# Studio link state and claim code at login (PROMPT.md 0.5.0).
if [ -x /usr/bin/beitlab-link ] && [ -t 1 ]; then
    /usr/bin/beitlab-link --motd 2>/dev/null
    [ -r /etc/beitlab/studio/claim-code ] && printf 'Studio claim code: %s (hostname %s)\n' \
        "$(cat /etc/beitlab/studio/claim-code)" "$(hostname)"
fi
