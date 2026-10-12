#!/bin/sh
# Dump everything this plugin reads from a NAS, in the format the plugin parses.
#
#   tools/capture_mib.sh NAS_HOST [COMMUNITY] > nas-dump.txt            # SNMP v2c
#   SNMP_OPTS="-v3 -l authPriv -u USER -a SHA -A AUTHPASS -x AES -X PRIVPASS" \
#       tools/capture_mib.sh NAS_HOST > nas-dump.txt                    # SNMP v3
#
# The dump contains personal data: the NAS and drive serial numbers (6574.1.5.2, 6574.2), the host name,
# contact / location (system group), and MAC addresses (interface table). Remove them before sharing the file.
# Never post a community string or passphrase.
HOST="${1:?usage: $0 NAS_HOST [COMMUNITY]   (SNMP_OPTS for other SNMP versions)}"
COMMUNITY="${2:-public}"
OPTS="${SNMP_OPTS:--v2c -c $COMMUNITY}"
for oid in \
    1.3.6.1.2.1.1 1.3.6.1.2.1.25.1.1 1.3.6.1.2.1.25.2.3.1 1.3.6.1.2.1.2.2.1 1.3.6.1.2.1.31.1.1.1 \
    1.3.6.1.4.1.2021.4 1.3.6.1.4.1.2021.10 1.3.6.1.4.1.2021.11 1.3.6.1.4.1.6574
do
    # shellcheck disable=SC2086  # OPTS is a list of options on purpose
    snmpbulkwalk $OPTS -On -Oe -m "" -Cc -t 5 -r 1 "$HOST" ".$oid"
done
