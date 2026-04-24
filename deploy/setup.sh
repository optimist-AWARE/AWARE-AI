#!/usr/bin/env bash
# One-time EC2 setup for AWARE-AI.
# Run on a fresh Ubuntu EC2 instance as the `ubuntu` user.
# Usage: bash deploy/setup.sh
set -euo pipefail

APP_DIR=/home/ubuntu/aware-ai
REPO_URL=https://github.com/optimist-AWARE/AWARE-AI.git
DOMAIN=aware.a-end.kr

sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip git nginx certbot python3-certbot-nginx

if [ ! -d "$APP_DIR/.git" ]; then
    git clone "$REPO_URL" "$APP_DIR"
fi

cd "$APP_DIR"
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

sudo cp deploy/aware-ai.service /etc/systemd/system/aware-ai.service
sudo systemctl daemon-reload
sudo systemctl enable aware-ai

echo 'ubuntu ALL=(ALL) NOPASSWD: /bin/systemctl restart aware-ai' | sudo tee /etc/sudoers.d/aware-ai >/dev/null
sudo chmod 440 /etc/sudoers.d/aware-ai

sudo cp deploy/nginx.conf /etc/nginx/sites-available/aware-ai
sudo ln -sf /etc/nginx/sites-available/aware-ai /etc/nginx/sites-enabled/aware-ai
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx

cat <<EOF

=============================================================
Initial setup done. Remaining manual steps:

1) Create /home/ubuntu/aware-ai/.env with required keys, e.g.:
     GEMINI_API_KEY=...
     OPENAI_API_KEY=...
     GEMINI_IMAGE_MODEL=gemini-2.5-flash-image
     OPENAI_TEXT_MODEL=gpt-5-mini
     DEBUG=1

2) Start the service:
     sudo systemctl start aware-ai
     systemctl status aware-ai

3) Point DNS: add an A record for ${DOMAIN} -> this EC2 public IP.

4) After DNS propagates, issue the HTTPS cert:
     sudo certbot --nginx -d ${DOMAIN}

5) Security group: open inbound 22 (SSH), 80, 443.

=============================================================
EOF
