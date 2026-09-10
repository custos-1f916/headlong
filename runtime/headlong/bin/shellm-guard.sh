#!/usr/bin/env bash
# shellm-guard.sh — sourced at the top of every block shellm executes (see
# _shellm_guard_preamble in bin/shellm). Shadows kill/pkill/killall/systemctl/
# thinkers so a block cannot signal its own run, an ancestor (the step, the
# dispatcher, the service), its process group, or the thinkers runtime. The
# functions are exported so scripts the block writes and runs (`bash /tmp/x.sh`)
# inherit them. SHELLM_GUARD=0 skips sourcing this file. Accident guard, not a
# security boundary: `command kill` bypasses it on purpose.
__shellm_guard_refuse() { printf '%s\n' "[shellm guard] refused: $* — this would signal your own run, its dispatcher, or the thinkers service. A child process you started is a normal 'kill <pid>'; find it with pgrep -P \$\$ or the pid you recorded." >&2; return 125; }
__shellm_protected() { case " ${SHELLM_PROTECTED_PIDS:-} " in *" $1 "*) return 0;; esac; [[ -n "${SHELLM_PROTECTED_PGID:-}" && "$1" == "$SHELLM_PROTECTED_PGID" ]]; }
kill() {
  local a t
  for a in "$@"; do
    case "$a" in
      -[0-9]*) t="${a#-}"; if [[ "$t" =~ ^[0-9]+$ ]] && __shellm_protected "$t"; then __shellm_guard_refuse "kill $*"; return $?; fi ;;
      0) __shellm_guard_refuse "kill $*"; return $? ;;
      [0-9]*) if __shellm_protected "$a"; then __shellm_guard_refuse "kill $*"; return $?; fi ;;
    esac
  done
  command kill "$@"
}
pkill() { case " $* " in *shellm*|*thinkers*|*headlong*|*dispatcher*|*" -g "*|*" -s "*) __shellm_guard_refuse "pkill $*"; return $?;; esac; command pkill "$@"; }
killall() { case " $* " in *" bash "*|*shellm*|*thinkers*|*headlong*) __shellm_guard_refuse "killall $*"; return $?;; esac; command killall "$@"; }
systemctl() { case " $* " in *headlong-thinkers*|*headlong-web*) case " $* " in *" stop "*|*" restart "*|*" kill "*|*" disable "*|*" mask "*) __shellm_guard_refuse "systemctl $*"; return $?;; esac;; esac; command systemctl "$@"; }
thinkers() { case " $* " in *" stop"*|*" restart"*) __shellm_guard_refuse "thinkers $*"; return $?;; esac; command thinkers "$@"; }
export -f __shellm_guard_refuse __shellm_protected kill pkill killall systemctl thinkers
