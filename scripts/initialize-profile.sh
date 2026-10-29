#!/command/with-contenv sh
# shellcheck shell=sh
# Run after upstream volume setup and profile reconciliation, before services.
set -eu
umask 077
if [ "$(id -u)" = 0 ]; then
    exec s6-setuidgid hermes /opt/hermes/.venv/bin/python /opt/agent-council/initialize-profile.py
fi
exec /opt/hermes/.venv/bin/python /opt/agent-council/initialize-profile.py
