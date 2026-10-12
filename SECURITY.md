# Security

## Scope

The plugin **reads** from a Synology NAS over SNMP (`snmpget`, `snmpbulkwalk`, `snmpwalk`). It never sends
`snmpset`, never logs in to DSM and needs no privileges on the NAS. It runs inside Domoticz, as the Domoticz user,
and starts the net-snmp client tools as child processes.

## Things to know

| Topic | Details | What to do |
|---|---|---|
| **Credentials on the command line** | The community string (v1/v2c) or the SNMPv3 passphrases are passed to the net-snmp tools as arguments, so other local users can see them in the process list (`ps`) while a request runs (a fraction of a second, repeated every poll). A later version may hand them over through a private net-snmp configuration file instead (see the roadmap) | On a shared host use an **SNMPv3 user with a read-only view**, or a dedicated read-only community; restrict SNMP on the NAS to the Domoticz host |
| **SNMP v1 / v2c is not encrypted** | The community travels in clear text and is easy to guess (`public`) | Prefer SNMPv3 `authPriv` (SHA + AES); allow UDP 161 from the Domoticz host only (DSM firewall) |
| **Credentials in Domoticz** | The community and passphrases are stored in Domoticz' hardware settings like any other plugin parameter. The plugin never writes them to the log | Protect the Domoticz database and its backups |
| **Command line tool** | `python3 -m synoSNMP -A pass` puts the passphrase in your shell history and in `ps` | Use a space-prefixed command (`HISTCONTROL=ignorespace`) or a throw-away SNMPv3 user |
| **Debug log** | With *Debug* enabled the log contains the NAS model, DSM version and **serial number** | Do not post debug logs unredacted |
| **MIB captures** | `tools/capture_mib.sh` output contains serial numbers (NAS and drives), the host name, contact / location and MAC addresses | Remove them before sharing; `.gitignore` excludes `dump*.txt` |
| **`deploy.sh`** | Runs commands on the target through SSH (`bash -s`), uses `sudo -n` only if the plugin directory is not writable, and never changes anything outside the plugin directory, the backup directory and (restart) the Domoticz service; with `--install-deps` it also runs `apt-get install snmp`. `--verify-args` with credentials is visible in the target's process list | Review the script before running it; use SSH keys |

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
