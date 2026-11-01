#!/usr/bin/env bash
# Run after the OpenTofu launch script has cloned the repository.
set -euo pipefail
if [ "$(id -u)" -ne 0 ]; then
  echo 'Run this script with sudo bash scripts/setup-single.sh.' >&2
  exit 1
fi
cd /opt/website
for command in python3 psql openssl curl; do
  command -v "$command" >/dev/null || { echo "Missing package for $command; finish cloud-init first." >&2; exit 1; }
done
systemctl enable --now postgresql
runuser -u ubuntu -- python3 -m venv /opt/website/.venv
runuser -u ubuntu -- /opt/website/.venv/bin/pip install -r requirements.txt
install -d -m 700 /etc/cloud-notes
if [ ! -f /etc/cloud-notes/app.env ]; then
  # Hexadecimal generated values avoid URL, SQL and systemd escaping problems.
  notes_db_password=$(openssl rand -hex 24)
  notes_session_secret=$(openssl rand -hex 32)
  install -m 600 /dev/null /etc/cloud-notes/app.env
  cat > /etc/cloud-notes/app.env <<EOF
DATABASE_URL=postgresql+psycopg://cloudnotes:${notes_db_password}@127.0.0.1:5432/cloudnotes
SECRET_KEY=${notes_session_secret}
INSTANCE_NAME=$(hostname)
COOKIE_SECURE=false
TRUST_PROXY=false
EOF
  runuser -u postgres -- psql -v ON_ERROR_STOP=1 -v db_password="$notes_db_password" <<'SQL'
SELECT format('CREATE ROLE cloudnotes LOGIN PASSWORD %L', :'db_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname='cloudnotes') \gexec
SELECT 'CREATE DATABASE cloudnotes OWNER cloudnotes'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname='cloudnotes') \gexec
SQL
  unset notes_db_password notes_session_secret
fi
install -m 644 deployment/cloud-notes.service /etc/systemd/system/cloud-notes.service
systemctl daemon-reload
systemctl enable cloud-notes
systemctl restart cloud-notes
for attempt in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8000/health; then
    echo
    echo 'Cloud Notes is running on port 8000; PostgreSQL is local on port 5432.'
    exit 0
  fi
  sleep 2
done
echo 'Startup failed. Check: sudo journalctl -u cloud-notes -n 50 --no-pager' >&2
exit 1
