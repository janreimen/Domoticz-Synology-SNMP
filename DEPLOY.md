# Deploying Domoticz-Synology-SNMP

The plugin is a plain directory (`plugin.py` + the `synoSNMP/` package) that has to sit in Domoticz' `plugins/`
directory. Nothing is compiled and no Python package is installed. This page covers the three ways to get it there,
how to verify the result, and how to update, roll back and remove it.

## 0. Prerequisites

| Where | What |
|---|---|
| Domoticz host | Domoticz with Python plugin support, Python 3.7 or newer |
| Domoticz host | the net-snmp **client tools**: `sudo apt install snmp` (provides `snmpget`, `snmpwalk`, `snmpbulkwalk`) |
| Synology NAS | DSM: *Control Panel > Terminal & SNMP > SNMP service* enabled, a v1/v2c community **or** an SNMPv3 user |
| Network | UDP 161 from the Domoticz host to the NAS (DSM firewall: allow the Domoticz host's IP) |

Quick check from the Domoticz host (should print the model):

```sh
snmpget -v2c -c public -On -m "" NAS_IP 1.3.6.1.4.1.6574.1.5.1.0
# .1.3.6.1.4.1.6574.1.5.1.0 = STRING: "DS920+"
```

If Domoticz runs in a **container**, `snmpget` must exist *inside* the container. A host-side `apt install snmp` does
not help; extend the image or install the package in the container.

## 1. `deploy.sh` (recommended)

Run it from a checkout (or unpacked archive) of this repository.

```sh
./deploy.sh --dry-run                               # show what would happen on this machine
./deploy.sh                                         # install on this machine
./deploy.sh --host jan@rpi4office                   # install on another machine over SSH
./deploy.sh --host jan@rpi4office --verify 192.168.1.20 --verify-args "-c public"
./deploy.sh --host jan@rpi4office --rollback        # back to the previous version
```

Or through make: `make deploy HOST=jan@rpi4office VERIFY=192.168.1.20`, `make deploy-dry`, `make rollback HOST=...`.

What it does on the target:

1. checks `python3` >= 3.7 and that `snmpget` / `snmpwalk` exist (with `--install-deps`: `apt-get install -y snmp`)
2. picks `sudo` only if the plugin directory is not writable for the SSH user (needs **passwordless** sudo then;
   otherwise log in as the Domoticz user or root)
3. backs up the installed copy to `~/domoticz-plugin-backups/Domoticz-Synology-SNMP-<version>-<time>.tar.gz`
   and keeps the newest 3. The backup is deliberately **outside** the plugins directory: a second copy of `plugin.py`
   in there would be loaded by Domoticz as a duplicate plugin
4. replaces `plugin.py`, `synoSNMP/` and the documentation files. Other files you put in that directory are left alone
5. copies `.env.sample` for schedule reference; on first install, create the local `.env` with `cp .env.sample .env` in the installed plugin directory and adjust intervals as needed (the plugin uses documented defaults if `.env` is absent)
6. gives the directory back to its previous owner (for example `domoticz:domoticz`)
7. restarts the `domoticz` systemd service
8. with `--verify NAS`: runs `python3 -m synoSNMP NAS --level 1` on the target and prints what the plugin would create

Nothing is changed when a step before the copy fails (for instance when the backup directory is not writable).

| Option | Default | Meaning |
|---|---|---|
| `--host [USER@]HOST` | this machine | deploy over SSH |
| `--ssh-opts "OPTS"` | | extra `ssh` options, e.g. `"-p 2222 -i ~/.ssh/id_ed25519"` |
| `--dir PATH` | `/srv/domoticz/plugins/Domoticz-Synology-SNMP` | target directory |
| `--dry-run`, `-n` | | report only; the target is not changed |
| `--rollback` | | restore the newest backup |
| `--install-deps` | | install the `snmp` package when the tools are missing |
| `--verify NAS` | | run the discovery CLI against the NAS afterwards |
| `--verify-args "ARGS"` | `-c public` | CLI options. Not for secrets: a passphrase here is visible in the process list and the shell history (the script warns and masks it in its output). For SNMPv3 run the CLI yourself, see step 4 |
| `--service NAME` | `domoticz` | systemd service to restart |
| `--restart-cmd CMD` | | run CMD on the target instead (init.d, docker restart, ...) |
| `--no-restart` | | do not restart Domoticz |
| `--backup-dir PATH` | `$HOME/domoticz-plugin-backups` | backup location on the target |
| `--keep N` | `3` | backups to keep |

Exit status: `0` success, `1` error (nothing was changed if it happened before the copy), `4` deployed, but the
`--verify` run failed (the message above it says why).

The script opens two SSH connections (upload, then run). Use key authentication, or `ControlMaster auto` in
`~/.ssh/config`, to avoid being asked twice. Credentials given with `--verify-args` are visible in the process list
of both machines while the check runs and stay in the shell history; the script warns about it. For SNMPv3 run the check
yourself with prompts (step 4) or use the Domoticz hardware page and look at the log.

## 2. By hand

```sh
cd /srv/domoticz/plugins
sudo cp -r /path/to/Domoticz-Synology-SNMP .          # plugin.py and synoSNMP/ must stay together
sudo chown -R domoticz:domoticz Domoticz-Synology-SNMP   # whatever user runs Domoticz
sudo systemctl restart domoticz
```

Or with git, which makes updates a `git pull`:

```sh
cd /srv/domoticz/plugins
git clone https://github.com/janreimen/Domoticz-Synology-SNMP.git
# later:
cd Domoticz-Synology-SNMP && git pull && sudo systemctl restart domoticz
```

The directory name does not matter to Domoticz; the plugin is identified by the `key` in the manifest
(`SynologySNMP`). Use one directory per plugin: do not leave an old copy, a `.bak` folder or an unpacked archive
next to it (Domoticz loads every `plugin.py` it finds one level below `plugins/`).

## 3. Add the hardware

1. *Setup > Hardware*, type **Synology NAS (net-snmp)**, name it (for example `DS1525`).
2. NAS address, SNMP version and community (or SNMPv3 user, passphrase and, if it differs, the privacy passphrase). Leave
   the rest at the defaults. After an update from 0.2.2 or older the *SNMPv3 privacy passphrase* field may still contain an
   old number such as `60`: it is ignored (shorter than 8 characters). It is a password field, so you cannot see it: clear
   the field before you type a passphrase into it, otherwise the old number becomes the start of the passphrase.
3. *Add*. The first poll creates the devices; the log shows `Connected to Synology <model> (<DSM version>)`.

All fields are explained in the [README](README.md#settings).

## 4. Verify

Without Domoticz, from the plugin directory:

```sh
cd /srv/domoticz/plugins/Domoticz-Synology-SNMP
python3 -m synoSNMP NAS_IP -c public                       # table of everything the plugin would create
python3 -m synoSNMP NAS_IP -c public --rates 10            # also shows network and disk I/O rates (second sample after 10 s)
python3 -m synoSNMP NAS_IP -v 3 -u monitor -A - -X -     # SNMPv3: asks for the passphrases (needs a terminal: ssh -t)
python3 -m synoSNMP NAS_IP --json --level 1                # machine readable
```

Exit status: `0` ok, `1` bad options or tools missing, `2` the NAS does not answer, `3` it answers but is not a Synology.
Run it as the user that runs Domoticz when you want to rule out permission problems
(`sudo -u domoticz python3 -m synoSNMP ...`).

If something is missing or wrong, `tools/capture_mib.sh NAS_IP [community] > dump.txt` records everything the plugin
reads. Redact the serial numbers before sharing the file.

## 5. Update, roll back, remove

* **Update:** run `deploy.sh` again (or `git pull`) and restart Domoticz. Devices are matched by `DeviceID`, so existing
  devices, their names, their history and their "used" flag are kept.
* **Roll back:** `./deploy.sh [--host ...] --rollback`, or unpack the newest archive from the backup directory
  over the plugin directory.
* **Remove:** delete the hardware in *Setup > Hardware* (this removes its devices), delete the plugin directory,
  restart Domoticz.
* **Upgrading from the old pysnmp plugin:** this is a different hardware type, so it creates its own devices. Run both
  in parallel for a while if you want to compare, then delete the old hardware. See the migration notes in the README.

## 6. Troubleshooting a deployment

| Symptom | Cause / fix |
|---|---|
| Hardware type "Synology NAS (net-snmp)" is not in the list | Domoticz was not restarted, the directory is not directly below `plugins/`, or `plugin.py` is not readable by the Domoticz user. See the Domoticz log at start-up |
| `Cannot run snmpget` / `net-snmp client tools` in the log | `sudo apt install snmp` (inside the container if Domoticz is containerised), then restart Domoticz |
| `No SNMP answer from ...` | address/community wrong, SNMP not enabled in DSM, or the DSM firewall blocks the Domoticz host. A wrong community looks exactly like a timeout |
| `does not serve the Synology MIB` | the agent answers but 1.3.6.1.4.1.6574 is not visible: restricted SNMP view, or not a Synology |
| Two hardware types / duplicate plugin errors | a second copy of the plugin sits in `plugins/` (backup folder, old version): remove it |
| `deploy.sh`: `not writable ... sudo needs a password` | log in as the Domoticz user or root, or allow passwordless sudo for the SSH user |
