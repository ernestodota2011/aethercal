#!/usr/bin/env bash
# ==The ONE place `deploy/.env` is put back.== Sourced by run.sh and stack-down.sh.
#
# It lived in both, identically, and that is the bug rather than the duplication: the same defect
# had to be found and fixed twice, and the third copy would have been born broken. `stack-up.sh`
# already learned this when its backup could be destroyed by a second boot (S6); this is the same
# class one script over, which is why it is now a function and not a paragraph repeated.
#
# ==A failed restore must never delete the only copy.== The old body ran `mv -f ... || true` and
# then removed the backup and its state unconditionally: if the move failed — read-only mount, a
# directory in the way, no space — the developer was left with neither `deploy/.env` nor the backup
# of it, and the script said "restored" on its way out. The removal now happens ONLY after the
# restore is confirmed; if it fails, both files are kept and their location is printed.

aethercal_restore_env() {
  local env_file="$1" env_backup="$2"
  [[ -f "${env_backup}.state" ]] || return 0

  if [[ "$(cat "${env_backup}.state")" == "existed" ]]; then
    if ! mv -f "${env_backup}" "${env_file}"; then
      echo "ERROR: could not restore ${env_file} from ${env_backup}." >&2
      echo "       BOTH files are being kept so nothing is lost. Restore it by hand:" >&2
      echo "         mv -f ${env_backup} ${env_file}" >&2
      echo "       Then remove ${env_backup}.state." >&2
      return 1
    fi
    echo "==> restored the previous deploy/.env"
  else
    rm -f "${env_file}"
    echo "==> removed the deploy/.env this run created (there was none before)"
  fi

  # Only now, with the restore confirmed, is it safe to drop the state marker.
  rm -f "${env_backup}.state" "${env_backup}"
}
