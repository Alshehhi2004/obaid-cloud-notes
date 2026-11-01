# Obaid's Cloud Notes

A dynamic personal notebook for CCEN 448 Lab 6. Built with Flask, Gunicorn and PostgreSQL.

## Features

- Register and log in with a username and password.
- Create, edit and delete notes; each account can only read its own notes.
- Passwords are hashed with Werkzeug's scrypt password hashing.
- Forms have CSRF protection. SQLAlchemy binds SQL values instead of concatenating user input.
- The footer and `X-Served-By` header identify the application server.
- `GET /health` checks database connectivity; `GET /instance` returns the responding server name.
- Sessions use signed cookies. Both application servers can use the same secret and shared PostgreSQL database in the bonus deployment.

## Runtime requirements

Ubuntu 24.04 LTS or another supported Ubuntu LTS, Python 3.12+, Python venv, PostgreSQL, Git, OpenSSL and curl. Port 8000 serves the application in the basic lab. PostgreSQL listens locally in the single-server setup and is not exposed through a public firewall rule.

The supplied lab OpenTofu template should set `website_packages` to `python3-venv python3-pip postgresql openssl curl`, `website_port` to `8000`, and `repository_url` to `https://github.com/Alshehhi2004/obaid-cloud-notes.git`.

## Single-server installation

The lab OpenTofu launch script installs the listed Ubuntu packages and clones this repository into `/opt/website`. After applying it, connect by SSH, then run:

```bash
sudo cloud-init status --wait
cd /opt/website
sudo bash scripts/setup-single.sh
```

The setup script creates a virtual environment, installs the Python dependencies, creates a database account and database, generates a session secret and database password, and starts the app as an unprivileged systemd service. It does not print or commit generated credentials. They live in `/etc/cloud-notes/app.env` with root-only permissions.

Open `http://INSTANCE_STATIC_IP:8000`, register a lab-only account, save a note, log out and log in again. This initial HTTP test is required by the lab; use only disposable lab credentials until HTTPS is enabled.

## Evidence commands

```bash
date
echo "$SSH_CONNECTION"
sudo systemctl status cloud-notes --no-pager
sudo -u postgres psql -d cloudnotes -c 'SELECT id, username, created_at FROM users;'
sudo -u postgres psql -d cloudnotes -c 'SELECT id, user_id, title, updated_at FROM notes;'
curl http://127.0.0.1:8000/health
```

Do not display password hashes, the environment file or private keys in submission screenshots.

## Cloudflare Quick Tunnel

On the main laptop, keep both commands running in separate terminals, replacing the key path and IP:

```text
ssh -i "PATH_TO_PEM" -N -L 127.0.0.1:8080:localhost:8000 ubuntu@INSTANCE_STATIC_IP
cloudflared tunnel --url http://127.0.0.1:8080
```

The app uses relative URLs and no fixed hostname, so a changing Quick Tunnel hostname does not require a code change. The basic configuration permits both the required HTTP demonstration and HTTPS tunnel demonstration. The tunnel's HTTPS URL has its own browser session: log in there separately.

After the direct HTTP evidence, set `public_http=false` in the OpenTofu template and apply. Browser traffic then follows Cloudflare -> cloudflared on the laptop -> SSH port forward -> app on the Lightsail instance. Only SSH needs to be public for this task. PostgreSQL remains private.

## Bonus readiness

This repository provides the application needed for the bonus, not completed bonus evidence. For the HTTPS bonus, put an HTTPS reverse proxy in front of the application with a trusted Let's Encrypt IP certificate and automatic renewal. Bind Gunicorn to loopback once it sits behind a proxy on the same server. Set `COOKIE_SECURE=true` after completing HTTP evidence. If enabling `TRUST_PROXY=true`, first ensure all requests reach the app through exactly one trusted proxy which overwrites forwarded headers.

For the availability bonus, run two application nodes with distinct `INSTANCE_NAME` values, the same `SECRET_KEY`, and the same `DATABASE_URL`, with a load balancer in front. Limit database network access to the application nodes. Provision the schema before handling traffic. A shared database and shared cookie secret let a logged-in user retain access to the same records when requests move between nodes. A single shared database is still a single point of failure: the lab test demonstrates application-node failover, not database high availability.

OpenTofu/Ansible files for the bonus will be added during the guided lab after completing and recording the main tasks.

## Cleanup

Save evidence, stop cloudflared and SSH forwarding, then destroy the OpenTofu-managed resources. Remove the lab Lightsail SSH key, IAM access key, policy and IAM user. Keep local state private until destroy completes. Do not publish credentials, environment files, state files or private keys.

This is a teaching application, not an Internet production service. Production use would also require abuse controls such as login throttling, backups and operational monitoring.
