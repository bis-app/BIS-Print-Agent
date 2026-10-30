#!/bin/sh
# Wartet auf Route und Uhr, damit der erste HTTPS-Poll nicht ins Leere laeuft.
# Danach startet das Fenster trotzdem, auch wenn Netz oder NTP ausbleiben.

i=0
while [ "$i" -lt 30 ]; do
    if ip route show default 2>/dev/null | grep -q .; then
        break
    fi
    i=$((i + 1))
    sleep 2
done

j=0
while [ "$j" -lt 15 ]; do
    if timedatectl show -p NTPSynchronized --value 2>/dev/null | grep -qx yes; then
        break
    fi
    j=$((j + 1))
    sleep 2
done

exec /opt/bis-print-agent/venv/bin/python /opt/bis-print-agent/print_agent.py
