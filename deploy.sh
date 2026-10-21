#!/usr/bin/env bash
# deploy.sh - install Domoticz-Synology-SNMP into a Domoticz plugins directory,
# on this machine or on another one over SSH.
#
#   ./deploy.sh                                   # install on this machine
#   ./deploy.sh --host jan@rpi4office             # install over SSH
#   ./deploy.sh --host jan@rpi4office --dry-run   # show what would happen
#   ./deploy.sh --host jan@rpi4office --verify 192.168.1.20
#   ./deploy.sh --host jan@rpi4office --rollback
#
# What it does on the target:
#   1. checks python3 >= 3.7 and the net-snmp client tools (snmpget / snmpwalk)
#   2. backs up the installed copy as a tar.gz outside the plugins directory
#      (a backup *inside* it would be loaded by Domoticz as a second plugin)
#   3. replaces plugin.py and synoSNMP/ (anything else in the directory is left alone)
#   4. restores the owner of the directory, restarts Domoticz
#   5. optionally runs the discovery CLI against the NAS (--verify)
set -euo pipefail

PLUGIN_NAME="Domoticz-Synology-SNMP"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

HOST=""
DIR="/srv/domoticz/plugins/${PLUGIN_NAME}"
# shellcheck disable=SC2016  # the single quotes are intentional: $HOME must expand on the target
BACKUP_DIR='$HOME/domoticz-plugin-backups'
KEEP=3
SERVICE="domoticz"
RESTART_CMD=""
RESTART=1
DRY=0
INSTALL_DEPS=0
ROLLBACK=0
VERIFY_HOST=""
VERIFY_ARGS="-c public"
SSH_OPTS=()

usage() {
    cat <<EOF
Usage: ${0##*/} [options]

Target
  --host [USER@]HOST    deploy over SSH (default: this machine)
  --ssh-opts "OPTS"     extra options for ssh, e.g. "-p 2222 -i ~/.ssh/id_ed25519"
  --dir PATH            plugin directory (default: ${DIR})

Actions
  --dry-run             only report what would be done (target is not changed)
  --rollback            restore the newest backup instead of deploying
  --install-deps        apt-get install snmp on the target if the tools are missing
  --verify NAS_HOST     after deploying run "python3 -m synoSNMP NAS_HOST" on the target
  --verify-args "ARGS"  options for that run (default: "${VERIFY_ARGS}"), e.g. "-v 3 -u monitor -A secret"

Domoticz
  --service NAME        systemd service to restart (default: ${SERVICE})
  --restart-cmd CMD     run CMD on the target instead of restarting the service
  --no-restart          do not restart Domoticz (restart it yourself before the new code is used)

Backups
  --backup-dir PATH     where backups go on the target (default: \$HOME/domoticz-plugin-backups)
  --keep N              number of backups to keep (default: ${KEEP})

  -V, --version         print the version of this source tree
  -h, --help            this text

Needs on the target: bash, tar, python3 >= 3.7; sudo without password if the plugin directory
is not writable for the SSH user (or log in as the Domoticz user / root).
EOF
}

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
say() { printf '%s\n' "$*"; }

source_version() {
    sed -n 's/^__version__ = "\(.*\)"$/\1/p' "${SRC}/synoSNMP/__init__.py" | head -n1
}

# ---- arguments -----------------------------------------------------------------------
while [ $# -gt 0 ]; do
    case "$1" in
        --host)          [ $# -ge 2 ] || die "--host needs a value"; HOST="$2"; shift 2 ;;
        --ssh-opts)      [ $# -ge 2 ] || die "--ssh-opts needs a value"; read -r -a SSH_OPTS <<<"$2"; shift 2 ;;
        --dir)           [ $# -ge 2 ] || die "--dir needs a value"; DIR="$2"; shift 2 ;;
        --backup-dir)    [ $# -ge 2 ] || die "--backup-dir needs a value"; BACKUP_DIR="$2"; shift 2 ;;
        --keep)          [ $# -ge 2 ] || die "--keep needs a value"; KEEP="$2"; shift 2 ;;
        --service)       [ $# -ge 2 ] || die "--service needs a value"; SERVICE="$2"; shift 2 ;;
        --restart-cmd)   [ $# -ge 2 ] || die "--restart-cmd needs a value"; RESTART_CMD="$2"; shift 2 ;;
        --verify)        [ $# -ge 2 ] || die "--verify needs a NAS address"; VERIFY_HOST="$2"; shift 2 ;;
        --verify-args)   [ $# -ge 2 ] || die "--verify-args needs a value"; VERIFY_ARGS="$2"; shift 2 ;;
        --no-restart)    RESTART=0; shift ;;
        --dry-run|-n)    DRY=1; shift ;;
        --install-deps)  INSTALL_DEPS=1; shift ;;
        --rollback)      ROLLBACK=1; shift ;;
        -V|--version)    source_version; exit 0 ;;
        -h|--help)       usage; exit 0 ;;
        *)               usage >&2; die "unknown option: $1" ;;
    esac
done

case "$KEEP" in ''|*[!0-9]*) die "--keep must be a number" ;; esac
[ "$KEEP" -ge 1 ] || die "--keep must be at least 1"
case "$DIR" in /|"") die "refusing to use '$DIR' as plugin directory" ;; esac

# ---- what gets deployed --------------------------------------------------------------
#[ -f "${SRC}/plugin.py" ] && [ -d "${SRC}/synoSNMP" ] || die "plugin.py / synoSNMP/ not found next to deploy.sh (${SRC})"
if [ ! -f "${SRC}/plugin.py" ] || [ ! -d "${SRC}/synoSNMP" ]; then
    die "plugin.py / synoSNMP/ not found next to deploy.sh (${SRC})"
fi
NEW_VERSION="$(source_version)"
[ -n "$NEW_VERSION" ] || die "cannot read __version__ from synoSNMP/__init__.py"

ITEMS=(plugin.py synoSNMP)
for optional in README.md CHANGELOG.md LICENSE DEPLOY.md SECURITY.md .env.sample docs tools; do
    [ -e "${SRC}/${optional}" ] && ITEMS+=("$optional")
done

# ---- how to reach the target ---------------------------------------------------------
if [ -n "$HOST" ]; then
    command -v ssh >/dev/null || die "ssh not found"
    # shellcheck disable=SC2029  # the command string is meant to expand on the target
    tgt() { ssh ${SSH_OPTS[@]+"${SSH_OPTS[@]}"} "$HOST" "$@"; }
    WHERE="$HOST"
else
    tgt() { bash -c "$*"; }
    WHERE="this machine"
fi

pack() {
    tar -C "$SRC" --exclude='__pycache__' --exclude='*.pyc' -cf - "${ITEMS[@]}"
}

# ---- the part that runs on the target ------------------------------------------------
# Sent through "bash -s"; settings arrive as environment variables.
remote_script() {
    cat <<'REMOTE'
set -eu
say()  { printf '%s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die()  { printf 'error: %s\n' "$*" >&2; exit 1; }
act()  { if [ "$DRY" = 1 ]; then say "  [dry-run] $*"; else "$@"; fi; }

[ -z "${STAGE:-}" ] || trap 'rm -rf "$STAGE"' EXIT
BACKUP_DIR="$(eval "printf '%s' \"$BACKUP_DIR\"")"      # expand $HOME of the target user
PARENT="$(dirname "$DIR")"
ME="$(id -un)"
say "Target : $ME@$(hostname) -> $DIR"

# 1. prerequisites ----------------------------------------------------------------------
command -v python3 >/dev/null 2>&1 || die "python3 not found on the target"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 7) else 1)' \
    || die "Python 3.7 or newer is required, the target has $(python3 -V 2>&1)"
say "Python : $(python3 -V 2>&1)"

SUDO=""
need_root=0
if [ -d "$DIR" ]; then
    for p in "$DIR" "$DIR"/*; do [ -w "$p" ] || need_root=1; done
else
    [ -d "$PARENT" ] || die "$PARENT does not exist - is Domoticz installed there? (use --dir)"
    [ -w "$PARENT" ] || need_root=1
fi
if [ "$need_root" = 1 ] && [ "$(id -u)" != 0 ]; then
    command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null \
        || die "$DIR is not writable for $ME and sudo needs a password: use the Domoticz user, root, or passwordless sudo"
    SUDO="sudo -n"
fi
RSUDO=""
[ "$(id -u)" = 0 ] || RSUDO="sudo -n"

if command -v snmpget >/dev/null 2>&1 && command -v snmpwalk >/dev/null 2>&1; then
    say "net-snmp: $(command -v snmpget) found"
elif [ "$INSTALL_DEPS" = 1 ]; then
    command -v apt-get >/dev/null 2>&1 || die "--install-deps needs apt-get; install the net-snmp client tools yourself"
    say "net-snmp client tools missing, installing package 'snmp'"
    act $RSUDO apt-get install -y snmp
else
    warn "snmpget/snmpwalk not found on the target - the plugin cannot work without them."
    warn "Install them (sudo apt install snmp) or run this script again with --install-deps."
fi

OWNER=""
if [ -d "$DIR" ]; then OWNER="$(stat -c '%U:%G' "$DIR" 2>/dev/null || true)"
else OWNER="$(stat -c '%U:%G' "$PARENT" 2>/dev/null || true)"; fi

OLD_VERSION="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$DIR/synoSNMP/__init__.py" 2>/dev/null | head -n1 || true)"
newest_backup() { ls -1t "$BACKUP_DIR"/"$NAME"-*.tar.gz 2>/dev/null | head -n1 || true; }

restart() {
    if [ "$RESTART" != 1 ]; then
        say "Domoticz was NOT restarted (--no-restart): restart it before the new code is used."
        return 0
    fi
    if [ -n "$RESTART_CMD" ]; then
        say "Restarting Domoticz: $RESTART_CMD"
        act bash -c "$RESTART_CMD" || warn "restart command failed"
    elif command -v systemctl >/dev/null 2>&1 && systemctl cat "$SERVICE" >/dev/null 2>&1; then
        say "Restarting Domoticz: systemctl restart $SERVICE"
        act $RSUDO systemctl restart "$SERVICE" \
            || warn "could not restart $SERVICE - run 'sudo systemctl restart $SERVICE' on the target"
    else
        warn "no systemd unit '$SERVICE' found - restart Domoticz yourself (or use --restart-cmd)"
    fi
}

fix_owner() {
    if [ -n "$OWNER" ] && { [ "$(id -u)" = 0 ] || [ -n "$SUDO" ]; }; then
        act $SUDO chown -R "$OWNER" "$DIR"
    fi
}

# 2a. rollback --------------------------------------------------------------------------
if [ "$ROLLBACK" = 1 ]; then
    last="$(newest_backup)"
    [ -n "$last" ] || die "no backup found in $BACKUP_DIR"
    say "Rolling back to $last"
    for item in $MANAGED; do act $SUDO rm -rf "$DIR/$item"; done
    act $SUDO mkdir -p "$DIR"
    act $SUDO tar -xzf "$last" -C "$DIR"
    fix_owner
    restart
    say "Rolled back (installed before the rollback: ${OLD_VERSION:-unknown})."
    exit 0
fi

# 2b. backup ----------------------------------------------------------------------------
if [ -f "$DIR/plugin.py" ]; then
    stamp="$(date +%Y%m%d-%H%M%S)"
    archive="$BACKUP_DIR/$NAME-${OLD_VERSION:-unknown}-$stamp.tar.gz"
    n=1
    while [ -e "$archive" ]; do          # two deployments within one second must not overwrite each other
        archive="$BACKUP_DIR/$NAME-${OLD_VERSION:-unknown}-$stamp-$n.tar.gz"
        n=$((n + 1))
    done
    say "Backup : $archive"
    act mkdir -p "$BACKUP_DIR" || die "cannot create the backup directory $BACKUP_DIR (use --backup-dir); nothing was changed"
    act tar -C "$DIR" --exclude='__pycache__' -czf "$archive" .
    if [ "$DRY" != 1 ]; then
        ls -1t "$BACKUP_DIR"/"$NAME"-*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
            rm -f "$old"
        done
    fi
else
    say "Backup : nothing installed yet"
fi

# 3. install ----------------------------------------------------------------------------
if [ -n "$OLD_VERSION" ]; then say "Version: $OLD_VERSION -> $NEW_VERSION"; else say "Version: $NEW_VERSION (new install)"; fi
for item in $MANAGED; do act $SUDO rm -rf "$DIR/$item"; done
act $SUDO mkdir -p "$DIR"
if [ "$DRY" = 1 ]; then
    say "  [dry-run] copy: $MANAGED"
else
    $SUDO cp -a "$STAGE"/. "$DIR"/
    $SUDO chmod -R u+rwX,go+rX "$DIR"
fi
fix_owner

# 4. restart ----------------------------------------------------------------------------
restart

# 5. verify -----------------------------------------------------------------------------
if [ -n "$VERIFY_HOST" ]; then
    if [ "$DRY" = 1 ]; then
        say "  [dry-run] verify: python3 -m synoSNMP $VERIFY_HOST $VERIFY_ARGS --level 1"
    else
        say "Verify : python3 -m synoSNMP $VERIFY_HOST --level 1"
        # PYTHONDONTWRITEBYTECODE: do not leave a __pycache__ owned by the wrong user in the plugin directory
        # shellcheck disable=SC2086
        ( cd "$DIR" && PYTHONDONTWRITEBYTECODE=1 python3 -m synoSNMP "$VERIFY_HOST" $VERIFY_ARGS --level 1 ) \
            || { warn "verification failed (exit $?): see the message above"; exit 4; }
    fi
fi
say "Done."
REMOTE
}

# ---- run -------------------------------------------------------------------------------
MANAGED="${ITEMS[*]}"
STAGE=""

if [ "$ROLLBACK" = 1 ]; then
    say "Rolling back ${PLUGIN_NAME} on ${WHERE}"
elif [ "$DRY" = 1 ]; then
    say "Dry run: ${PLUGIN_NAME} ${NEW_VERSION} -> ${WHERE}. Files that would be copied:"
    pack | tar -tf - | sed 's/^/  /'
else
    say "Deploying ${PLUGIN_NAME} ${NEW_VERSION} to ${WHERE}"
    # shellcheck disable=SC2016  # runs on the target, so $d / $TMPDIR must not expand here
    STAGE="$(pack | tgt 'd=$(mktemp -d "${TMPDIR:-/tmp}/synosnmp.XXXXXX") && tar -xf - --no-same-owner -C "$d" && printf "%s\n" "$d"')"
    [ -n "$STAGE" ] || die "upload failed"
fi

env_args=""
add_env() { env_args+=" $1=$(printf '%q' "$2")"; }
add_env NAME "$PLUGIN_NAME"
add_env DIR "$DIR"
add_env BACKUP_DIR "$BACKUP_DIR"
add_env KEEP "$KEEP"
add_env SERVICE "$SERVICE"
add_env RESTART_CMD "$RESTART_CMD"
add_env RESTART "$RESTART"
add_env DRY "$DRY"
add_env INSTALL_DEPS "$INSTALL_DEPS"
add_env ROLLBACK "$ROLLBACK"
add_env VERIFY_HOST "$VERIFY_HOST"
add_env VERIFY_ARGS "$VERIFY_ARGS"
add_env MANAGED "$MANAGED"
add_env NEW_VERSION "$NEW_VERSION"
add_env STAGE "$STAGE"

remote_script | tgt "env${env_args} bash -s"
