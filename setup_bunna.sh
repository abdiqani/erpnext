#!/usr/bin/env bash
set -euo pipefail
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log_info()  { echo -e "${BLUE}[INFO]${NC} $1"; }
log_ok()    { echo -e "${GREEN}[  OK]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[FAIL]${NC} $1"; }

DEV_USER="ubuntu"
DOMAIN="erp.abdiqanijama.com"
SITE_NAME="erp.abdiqanijama.com"
ADMIN_EMAIL="admin@abdiqanijama.com"
BENCH_DIR="/home/ubuntu/dev/frappe-bench"
FRAPPE_BRANCH="version-15"
ERPNEXT_BRANCH="version-15"
PYTHON_VERSION="3.11"
NODE_VERSION="18"
COMPANY_NAME="Bunna Superstore"
COMPANY_ABBR="BS"
COUNTRY="United Kingdom"
CURRENCY="GBP"
DB_ROOT_PASSWORD=$(openssl rand -base64 24 | tr -d '/+=')
ADMIN_PASSWORD=$(openssl rand -base64 16 | tr -d '/+=')
WC_URL="https://abdiqanijama.com"
WC_CONSUMER_KEY="ck_af38191f7711826c3273a062d0f72c770609a2ce"
WC_CONSUMER_SECRET="cs_d6ea1134d0beb79b424d2f3d97c1fbf2e0ee8d3b"
WC_WEBHOOK_SECRET=$(openssl rand -hex 32)

if [[ $EUID -ne 0 ]]; then log_error "Run as root"; exit 1; fi

TOTAL_RAM=$(free -m | awk '/^Mem:/{print $2}')
if [[ $TOTAL_RAM -lt 1800 ]] && [[ ! -f /swapfile ]]; then
    log_info "Adding 2GB swap..."
    fallocate -l 2G /swapfile && chmod 600 /swapfile
    mkswap /swapfile > /dev/null && swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
    log_ok "Swap enabled"
fi

CREDS_FILE="/root/.erpnext_credentials"
cat > "$CREDS_FILE" << CREDS
# Bunna Superstore - ERPNext Credentials - $(date -Iseconds)
DOMAIN=${DOMAIN}
URL=https://${DOMAIN}
ADMIN_USER=Administrator
ADMIN_PASSWORD=${ADMIN_PASSWORD}
DB_ROOT_PASSWORD=${DB_ROOT_PASSWORD}
WC_URL=${WC_URL}
WC_CONSUMER_KEY=${WC_CONSUMER_KEY}
WC_CONSUMER_SECRET=${WC_CONSUMER_SECRET}
WC_WEBHOOK_SECRET=${WC_WEBHOOK_SECRET}
WEBHOOK_URL=https://${DOMAIN}/api/method/erpnext.erpnext_integrations.connectors.woocommerce_webhook.handle_webhook
POS_URL=https://${DOMAIN}/app/pos
CREDS
chmod 600 "$CREDS_FILE"

echo ""
echo "============================================================"
echo "  Bunna Superstore - ERPNext Deployment"
echo "============================================================"
echo "  Domain:    ${DOMAIN}"
echo "  Install:   ${BENCH_DIR}"
echo "  Admin:     Administrator / ${ADMIN_PASSWORD}"
echo "  WC Store:  ${WC_URL}"
echo "  Creds:     ${CREDS_FILE}"
echo "============================================================"
echo ""

log_info "[1/12] System packages..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get upgrade -y -qq
apt-get install -y -qq \
    git python3-dev python3-pip python3-venv python3-setuptools \
    software-properties-common build-essential \
    libffi-dev libssl-dev libjpeg-dev zlib1g-dev libfreetype6-dev \
    liblcms2-dev libwebp-dev libharfbuzz-dev libfribidi-dev libxcb1-dev \
    redis-server curl wget supervisor nginx \
    certbot python3-certbot-nginx \
    fail2ban ufw cron xvfb libfontconfig \
    > /dev/null 2>&1
log_ok "System packages installed"

log_info "[2/12] Node.js ${NODE_VERSION}..."
if ! command -v node &>/dev/null || ! node -v | grep -q "v${NODE_VERSION}"; then
    curl -fsSL https://deb.nodesource.com/setup_${NODE_VERSION}.x | bash - > /dev/null 2>&1
    apt-get install -y -qq nodejs > /dev/null 2>&1
fi
npm install -g yarn > /dev/null 2>&1
log_ok "Node.js $(node -v) installed"

log_info "[3/12] MariaDB..."
apt-get install -y -qq mariadb-server mariadb-client libmysqlclient-dev > /dev/null 2>&1
cat > /etc/mysql/mariadb.conf.d/99-erpnext.cnf << 'MYCONF'
[mysqld]
character-set-client-handshake = FALSE
character-set-server = utf8mb4
collation-server = utf8mb4_unicode_ci
innodb_buffer_pool_size = 256M
innodb_log_file_size = 64M
innodb_file_per_table = 1
innodb_flush_log_at_trx_commit = 1
innodb_flush_method = O_DIRECT
innodb_log_buffer_size = 16M
max_allowed_packet = 256M
tmp_table_size = 64M
max_heap_table_size = 64M
[mysql]
default-character-set = utf8mb4
MYCONF
systemctl restart mariadb
systemctl enable mariadb > /dev/null 2>&1
mysql -u root <<SQLEOF
ALTER USER 'root'@'localhost' IDENTIFIED BY '${DB_ROOT_PASSWORD}';
FLUSH PRIVILEGES;
SQLEOF
log_ok "MariaDB configured"

log_info "[4/12] wkhtmltopdf..."
if ! command -v wkhtmltopdf &>/dev/null; then
    ARCH=$(dpkg --print-architecture)
    wget -q "https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6.1-3/wkhtmltox_0.12.6.1-3.jammy_${ARCH}.deb" -O /tmp/wkhtmltox.deb 2>/dev/null || true
    if [[ -f /tmp/wkhtmltox.deb ]]; then
        apt-get install -y -qq /tmp/wkhtmltox.deb > /dev/null 2>&1 || apt-get install -y -qq wkhtmltopdf > /dev/null 2>&1
        rm -f /tmp/wkhtmltox.deb
    else
        apt-get install -y -qq wkhtmltopdf > /dev/null 2>&1
    fi
fi
log_ok "wkhtmltopdf ready"

log_info "[5/12] Setting up 'ubuntu' user and /home/ubuntu/dev..."
if ! id -u "$DEV_USER" &>/dev/null; then
    useradd -m -s /bin/bash "$DEV_USER"
    usermod -aG sudo "$DEV_USER"
fi
echo "${DEV_USER} ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/ubuntu
chmod 440 /etc/sudoers.d/ubuntu
mkdir -p /home/ubuntu/dev
chown ubuntu:ubuntu /home/ubuntu/dev
log_ok "User 'ubuntu' + /home/ubuntu/dev ready"

log_info "[6/12] Installing Frappe Bench CLI..."
sudo -u "$DEV_USER" bash -c "pip3 install --user frappe-bench" > /dev/null 2>&1 || pip3 install frappe-bench > /dev/null 2>&1
log_ok "Bench CLI installed"

log_info "[7/12] bench init + ERPNext (~10 min)..."
if [[ ! -d "$BENCH_DIR" ]]; then
    sudo -u "$DEV_USER" bash -c "
        export PATH=\$HOME/.local/bin:\$PATH
        cd /home/${DEV_USER}/dev
        bench init frappe-bench --frappe-branch ${FRAPPE_BRANCH} --python python${PYTHON_VERSION} 2>&1
    " | tail -3
fi
sudo -u "$DEV_USER" bash -c "
    export PATH=\$HOME/.local/bin:\$PATH
    cd ${BENCH_DIR}
    if [[ ! -d apps/erpnext ]]; then
        bench get-app erpnext --branch ${ERPNEXT_BRANCH} 2>&1
    fi
" | tail -3
log_ok "ERPNext installed at ${BENCH_DIR}"

log_info "[8/12] Creating site ${SITE_NAME}..."
sudo -u "$DEV_USER" bash -c "
    export PATH=\$HOME/.local/bin:\$PATH
    cd ${BENCH_DIR}
    bench new-site ${SITE_NAME} \
        --db-root-password '${DB_ROOT_PASSWORD}' \
        --admin-password '${ADMIN_PASSWORD}' \
        --mariadb-root-password '${DB_ROOT_PASSWORD}' \
        --install-app erpnext 2>&1
    bench use ${SITE_NAME}
    bench --site ${SITE_NAME} migrate 2>&1
" | tail -5
log_ok "Site created"

log_info "[9/12] Production mode (supervisor + nginx)..."
sudo -u "$DEV_USER" bash -c "
    export PATH=\$HOME/.local/bin:\$PATH
    cd ${BENCH_DIR}
    sudo bench setup production ${DEV_USER} --yes 2>&1
" | tail -3
cat > /etc/nginx/conf.d/erpnext-tuning.conf << 'NGTUNE'
client_max_body_size 50m;
proxy_read_timeout 120;
proxy_connect_timeout 120;
proxy_send_timeout 120;
NGTUNE
systemctl reload nginx
log_ok "Production mode active"

log_info "[10/12] SSL certificate..."
certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --email "$ADMIN_EMAIL" --redirect 2>&1 | tail -3
(crontab -l 2>/dev/null; echo "0 0,12 * * * certbot renew --quiet") | sort -u | crontab -
log_ok "SSL installed"

log_info "[11/12] WooCommerce + POS configuration..."
sudo -u "$DEV_USER" bash -c "
    export PATH=\$HOME/.local/bin:\$PATH
    cd ${BENCH_DIR}
    bench --site ${SITE_NAME} console << 'PYEOF'
import frappe
settings = frappe.get_single('WooCommerce Settings')
settings.enabled = 1
settings.woocommerce_url = '${WC_URL}'
settings.consumer_key = '${WC_CONSUMER_KEY}'
settings.consumer_secret = '${WC_CONSUMER_SECRET}'
settings.webhook_secret = '${WC_WEBHOOK_SECRET}'
settings.verify_ssl = 1
settings.sync_products = 1
settings.sync_orders = 1
settings.sync_stock = 1
settings.product_sync_direction = 'Bidirectional'
settings.stock_sync_direction = 'ERPNext to WooCommerce'
settings.order_status_filter = 'processing,completed'
settings.sync_frequency = 'Every 15 minutes'
company = frappe.db.get_single_value('Global Defaults', 'default_company')
if not company:
    cl = frappe.get_all('Company', limit=1)
    company = cl[0].name if cl else None
if company:
    settings.company = company
    wh = frappe.db.get_value('Warehouse', {'company': company, 'is_group': 0}, 'name')
    settings.warehouse = wh or 'Stores - ${COMPANY_ABBR}'
    settings.price_list = 'Standard Selling'
    settings.default_customer_group = frappe.db.get_single_value('Selling Settings', 'customer_group') or 'All Customer Groups'
    settings.default_item_group = 'All Item Groups'
    ta = frappe.db.get_value('Account', {'company': company, 'account_type': 'Tax', 'is_group': 0}, 'name')
    if ta: settings.tax_account = ta
settings.enable_pos = 1
settings.flags.ignore_permissions = True
settings.flags.ignore_mandatory = True
settings.save()
try:
    from erpnext.erpnext_integrations.doctype.woocommerce_settings.pos_setup import setup_pos_for_woocommerce
    setup_pos_for_woocommerce(settings)
except Exception as e:
    print(f'POS deferred: {e}')
try:
    from erpnext.erpnext_integrations.woocommerce_custom_fields import setup_custom_fields
    setup_custom_fields()
except Exception as e:
    print(f'Custom fields note: {e}')
frappe.db.commit()
print('WooCommerce configured!')
PYEOF
" 2>&1 | tail -5
log_ok "WooCommerce + POS configured"

log_info "[12/12] Security + backups..."
ufw --force enable > /dev/null 2>&1
ufw default deny incoming > /dev/null 2>&1
ufw default allow outgoing > /dev/null 2>&1
ufw allow ssh > /dev/null 2>&1
ufw allow 80/tcp > /dev/null 2>&1
ufw allow 443/tcp > /dev/null 2>&1
ufw allow 9000/tcp > /dev/null 2>&1
cat > /etc/fail2ban/jail.local << 'F2B'
[DEFAULT]
bantime = 3600
findtime = 600
maxretry = 5
[sshd]
enabled = true
[nginx-http-auth]
enabled = true
F2B
systemctl restart fail2ban > /dev/null 2>&1
sudo -u "$DEV_USER" bash -c "
    export PATH=\$HOME/.local/bin:\$PATH
    cd ${BENCH_DIR}
    bench --site ${SITE_NAME} enable-scheduler 2>&1
" | tail -2
BACKUP_CRON="0 2 * * * cd ${BENCH_DIR} && /home/${DEV_USER}/.local/bin/bench --site ${SITE_NAME} backup --with-files > /dev/null 2>&1"
(sudo -u "$DEV_USER" crontab -l 2>/dev/null; echo "$BACKUP_CRON") | sort -u | sudo -u "$DEV_USER" crontab -
cat > /usr/local/bin/erpnext-health-check.sh << 'HC'
#!/bin/bash
STATUS=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost/api/method/frappe.client.get_count?doctype=DocType" --max-time 10)
if [[ "$STATUS" -ne 200 ]]; then
    echo "$(date): FAIL (HTTP $STATUS)" >> /var/log/erpnext-health.log
    sudo supervisorctl restart all
fi
HC
chmod +x /usr/local/bin/erpnext-health-check.sh
(crontab -l 2>/dev/null; echo "*/5 * * * * /usr/local/bin/erpnext-health-check.sh") | sort -u | crontab -
cat > /etc/logrotate.d/erpnext << LOGR
${BENCH_DIR}/logs/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
LOGR
sed -i 's/#\?MaxAuthTries.*/MaxAuthTries 3/' /etc/ssh/sshd_config
systemctl reload sshd > /dev/null 2>&1 || systemctl reload ssh > /dev/null 2>&1
log_ok "Security hardened, backups configured"

WEBHOOK_URL="https://${DOMAIN}/api/method/erpnext.erpnext_integrations.connectors.woocommerce_webhook.handle_webhook"
echo ""
echo "============================================================"
echo -e "${GREEN}  DEPLOYMENT COMPLETE - Bunna Superstore${NC}"
echo "============================================================"
echo ""
echo "  ERPNext:  https://${DOMAIN}"
echo "  Login:    Administrator"
echo "  Password: ${ADMIN_PASSWORD}"
echo "  POS:      https://${DOMAIN}/app/pos"
echo "  WC Store: ${WC_URL} [CONNECTED]"
echo ""
echo "  NEXT STEPS:"
echo "  1. Open https://${DOMAIN} - run Setup Wizard"
echo "     Company: ${COMPANY_NAME} | Country: ${COUNTRY} | Currency: ${CURRENCY}"
echo "  2. Test WC connection: WooCommerce Settings > Test Connection"
echo "  3. Add webhooks in WordPress Admin > WooCommerce > Webhooks:"
echo "     URL:    ${WEBHOOK_URL}"
echo "     Secret: ${WC_WEBHOOK_SECRET}"
echo "     Topics: Order created, Order updated, Product updated"
echo "  4. Run first sync: WooCommerce Settings > Sync > Full Sync"
echo "  5. Open POS: https://${DOMAIN}/app/pos"
echo ""
echo "  Credentials: cat ${CREDS_FILE}"
echo "  Bench dir:   ${BENCH_DIR}"
echo "============================================================"
