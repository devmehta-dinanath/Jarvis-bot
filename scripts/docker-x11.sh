#!/usr/bin/env bash
# Resolve the host X authority file for Docker bind-mount (avoids broken compose interpolation).

resolve_xauthority_source() {
  if [ -n "${XAUTHORITY:-}" ] && [ -f "$XAUTHORITY" ]; then
    printf '%s\n' "$XAUTHORITY"
    return 0
  fi
  # GDM sets XAUTHORITY to a directory, e.g. /run/user/1000/gdm/Xauthority
  if [ -n "${XAUTHORITY:-}" ] && [ -d "$XAUTHORITY" ] && [ -f "$XAUTHORITY/.Xauthority" ]; then
    printf '%s\n' "$XAUTHORITY/.Xauthority"
    return 0
  fi
  if [ -f "${HOME}/.Xauthority" ]; then
    printf '%s\n' "${HOME}/.Xauthority"
    return 0
  fi
  return 1
}

prepare_xauthority_mount() {
  local target="${1:-data/.jarvis-xauthority}"
  local src

  mkdir -p "$(dirname "$target")"
  if src="$(resolve_xauthority_source)"; then
    cp "$src" "$target"
    chmod 600 "$target"
    echo "X11: using authority file $src"
  else
    : >"$target"
    chmod 600 "$target"
    echo "WARN: no X authority file found; created stub at $target"
    echo "      Run: xhost +local:docker   (docker-up.sh does this on Linux)"
  fi
}
