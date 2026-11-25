# Shared by the release installer and the standalone updater.

bundled_wheels_ready() {
  local pip="$1" install_dir="$2"
  [ -d "$install_dir/wheels" ] &&
    "$pip" install --dry-run --disable-pip-version-check --no-index \
      --find-links "$install_dir/wheels" -r "$install_dir/requirements.txt" >/dev/null 2>&1
}

uvicorn_port() {
  local cmdline="$1" fallback="$2" arg previous="" candidate=""
  while IFS= read -r -d '' arg; do
    if [ "$previous" = "--port" ]; then candidate="$arg"; fi
    case "$arg" in --port=*) candidate="${arg#--port=}" ;; esac
    previous="$arg"
  done < "$cmdline"
  if [[ "$candidate" =~ ^[1-9][0-9]{0,4}$ ]] && [ "$candidate" -le 65535 ]; then
    printf '%s\n' "$candidate"
  else
    printf '%s\n' "$fallback"
  fi
}

probe_local_login() {
  local port="$1" scheme reply options pid
  systemctl is-active --quiet runvard || return 1
  pid="$(systemctl show runvard --property=MainPID --value 2>/dev/null)" || return 1
  [[ "$pid" =~ ^[1-9][0-9]*$ ]] || return 1
  if [ -r "/proc/$pid/cmdline" ]; then
    port="$(uvicorn_port "/proc/$pid/cmdline" "$port")" || port="$1"
  fi
  for scheme in http https; do
    options=(--noproxy '*')
    # Local readiness only. An installation can use a self-signed TLS certificate.
    [ "$scheme" != https ] || options+=(--insecure)
    reply="$(curl -fs -w '\n%{http_code}' --max-time 2 \
      "${options[@]}" "${scheme}://127.0.0.1:${port}/login")" || continue
    if [[ "$reply" == *'<title>Runvard'* && "$reply" == *'id="login-form"'* && "$reply" == *$'\n200' ]] &&
       [ "$(systemctl show runvard --property=MainPID --value)" = "$pid" ] &&
       systemctl is-active --quiet runvard; then
      printf '%s 200 %s\n' "$scheme" "$port"
      return 0
    fi
  done
  return 1
}

record_version() {
  local path="$1" commit="$2" tmp
  tmp="$(mktemp "${path}.XXXXXX")" || return 1
  if printf '%s\n' "$commit" > "$tmp" && mv -f "$tmp" "$path"; then return 0; fi
  rm -f -- "$tmp"
  return 1
}
