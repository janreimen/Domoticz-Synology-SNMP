#!/bin/sh
# Dump everything this plugin reads from a NAS, in the same format the plugin parses.
#   usage: tools/capture_mib.sh NAS_HOST [COMMUNITY] > nas-dump.txt
# The dump contains your NAS serial number (6574.1.5.2) and drive serials - redact before sharing.
HOST="${1:?usage: $0 NAS_HOST [COMMUNITY]}"
COMMUNITY="${2:-public}"
for oid in \
    1.3.6.1.2.1.1 1.3.6.1.2.1.25.1.1 1.3.6.1.2.1.25.2.3.1 1.3.6.1.2.1.2.2.1 1.3.6.1.2.1.31.1.1.1 \
    1.3.6.1.4.1.2021.4 1.3.6.1.4.1.2021.10 1.3.6.1.4.1.2021.11 1.3.6.1.4.1.6574
do
    snmpbulkwalk -v2c -c "$COMMUNITY" -On -Oe -m "" -Cc -t 5 -r 1 "$HOST" ".$oid"
done
