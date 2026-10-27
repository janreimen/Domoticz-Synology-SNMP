# Security

## Scope

The plugin **reads** from a Synology NAS over SNMP (`snmpget`, `snmpbulkwalk`, `snmpwalk`). It never sends
`snmpset`, never logs in to DSM and needs no privileges on the NAS. It runs inside Domoticz, as the Domoticz user,
and starts the net-snmp client tools as child processes.

## Things to know

| Topic | Details | What to do |
|---|---|---|
| **Plugin credential handling (0.2.1)** | The plugin writes the community string or SNMPv3 passphrases to a private temporary `snmp.conf` (directory `0700`, file `0600`) and supplies its directory through `SNMPCONFPATH`; secrets are not included in net-snmp process arguments. The file is removed when the client stops, and, since 0.2.3, a directory left behind by a Domoticz that was killed is removed at the next start (only real directories of the same user that contain nothing but `snmp.conf` and whose name carries the id of a process that no longer exists; directories of 0.2.2 and older have no process id and are left for you to remove). This protects against ordinary process-argument inspection, not root access or a compromised Domoticz account. | Keep the Domoticz host and its plugin process trusted; prefer SNMPv3 `authPriv` and restrict UDP 161 to the Domoticz host |
| **SNMP v1 / v2c is not encrypted** | The community travels in clear text and is easy to guess (`public`) | Prefer SNMPv3 `authPriv` (SHA + AES); allow UDP 161 from the Domoticz host only (DSM firewall) |
| **Credentials in Domoticz** | The community and passphrases are stored in Domoticz' hardware settings like any other plugin parameter. The plugin never writes them to the log. Since 0.2.3 the SNMPv3 privacy passphrase has its own password field; the older `;passphrase` part of *SNMPv3 options* is shown in clear text on the hardware page, still works, and is reported as deprecated. The `.env` file holds no credentials and the plugin never reads any from it | Protect the Domoticz database and its backups |
| **Command line diagnostic tool** | Supplying `-c`, `-A` or `-X` a value to `python3 -m synoSNMP` exposes the secret in the shell history and in the process list of the diagnostic Python process, even though the child net-snmp processes use the private config file. | Pass `-` as the value (`-A - -X -`) to be asked, or set `SYNO_SNMP_COMMUNITY`, `SYNO_SNMP_AUTH_PASS` and `SYNO_SNMP_PRIV_PASS` (since 0.2.3); do not paste real credentials into shared terminals or logs |
| **Debug log** | With *Debug* enabled the log contains the NAS model, DSM version and **serial number** | Do not post debug logs unredacted |
| **MIB captures** | `tools/capture_mib.sh` output contains serial numbers (NAS and drives), the host name, contact / location and MAC addresses | Remove them before sharing; `.gitignore` excludes `dump*.txt` |
| **`deploy.sh`** | Runs commands on the target through SSH (`bash -s`), uses `sudo -n` only if the plugin directory is not writable, and never changes anything outside the plugin directory, the backup directory and (restart) the Domoticz service; with `--install-deps` it also runs `apt-get install snmp`. `--verify-args` with credentials is visible in the process list of both machines and in the shell history; since 0.2.3 the script warns about it and masks the values in its own output. It cannot ask for a passphrase over SSH: for SNMPv3 run `python3 -m synoSNMP ... -A - -X -` yourself on the target | Review the script before running it; use SSH keys |

## Hardening checklist

1. Create an SNMPv3 user on the NAS (DSM: *Control Panel > Terminal & SNMP*) with a long passphrase.
2. Restrict UDP 161 to the Domoticz host (*Control Panel > Security > Firewall*).
3. Disable SNMP v1/v2c if you do not need it.
4. Keep the plugin directory writable only for the user that deploys it.

## Supported versions

Only the latest release receives fixes.

## Reporting a vulnerability

Please do **not** open a public issue for a security problem. Use GitHub's private vulnerability reporting
(*Security* tab of the repository > *Report a vulnerability*). Include the plugin version, what you observed and, if
possible, how to reproduce it. Do not include real credentials.
