#!/usr/bin/env bash
#
# ERPNext + WooCommerce Integration - Production VPS Deployment Script
#
# This script fully automates the deployment of ERPNext with WooCommerce
# integration and POS on a fresh Ubuntu 22.04/24.04 VPS.
#
# Usage:
#   chmod +x deploy_erpnext_woocommerce.sh
#   sudo ./deploy_erpnext_woocommerce.sh
#
# Or with all options:
#   sudo ./deploy_erpnext_woocommerce.sh \
#     --domain erp.yourdomain.com \
#     --email admin@yourdomain.com \
#     --db-password "YourDBPassword123!" \
#     --admin-password "YourAdminPassword123!" \
#     --wc-url "https://yourstore.com" \
#     --wc-key "ck_xxxx" \
#     --wc-secret "cs_xxxx"
#
# What this script does:
#   1. Installs all system dependencies (Python, Node, Redis, MariaDB, wkhtmltopdf, nginx)
#   2. Creates a dedicated 'frappe' system user
#   3. Installs Frappe Bench and creates a new bench
#   4. Creates a new ERPNext site with your domain
#   5. Installs ERPNext app (with WooCommerce integration included)
#   6. Configures SSL via Let's Encrypt (if domain provided)
#   7. Sets up production mode (supervisor + nginx)
#   8. Configures WooCommerce integration settings
#   9. Sets up POS profile
#  10. Configures firewall (UFW)
#  11. Sets up automated backups
#  12. Creates a systemd health check timer
#
# Requirements:
#   - Fresh Ubuntu 22.04 or 24.04 VPS
#   - Minimum 2 GB RAM (4 GB recommended)
#   - Root or sudo access
#   - Domain name pointed to VPS IP (for SSL)
#

set -euo pipefail

# ============================================================================
# Color output
# ============================================================================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info()  { echo -e "${BLUE}[INFO]${NC} $1"; }
log_ok()    { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ============================================================================
# Default configuration
# ============================================================================
FRAPPE_USER="frappe"
FRAPPE_BRANCH="version-15"
ERPNEXT_BRANCH="version-15"
PYTHON_VERSION="3.11"
NODE_VERSION="18"
BENCH_DIR="/home/${FRAPPE_USER}/frappe-bench"
SITE_NAME=""
DOMAIN=""
ADMIN_EMAIL=""
DB_ROOT_PASSWORD=""
ADMIN_PASSWORD=""
WC_URL=""
WC_CONSUMER_KEY=""
WC_CONSUMER_SECRET=""
WC_WEBHOOK_SECRET=""
SKIP_SSL=false
SKIP_WC_CONFIG=false
COMPANY_NAME="My Company"
COMPANY_ABBR="MC"
COUNTRY="United States"
CURRENCY="USD"

# ============================================================================
# Parse command-line arguments
# ============================================================================
while [[ $# -gt 0 ]]; do
    case $1 in
        --domain)       DOMAIN="$2"; shift 2 ;;
        --email)        ADMIN_EMAIL="$2"; shift 2 ;;
        --db-password)  DB_ROOT_PASSWORD="$2"; shift 2 ;;
        --admin-password) ADMIN_PASSWORD="$2"; shift 2 ;;
        --wc-url)       WC_URL="$2"; shift 2 ;;
        --wc-key)       WC_CONSUMER_KEY="$2"; shift 2 ;;
        --wc-secret)    WC_CONSUMER_SECRET="$2"; shift 2 ;;
        --wc-webhook-secret) WC_WEBHOOK_SECRET="$2"; shift 2 ;;
        --company)      COMPANY_NAME="$2"; shift 2 ;;
        --company-abbr) COMPANY_ABBR="$2"; shift 2 ;;
        --country)      COUNTRY="$2"; shift 2 ;;
        --currency)     CURRENCY="$2"; shift 2 ;;
        --skip-ssl)     SKIP_SSL=true; shift ;;
        --skip-wc)      SKIP_WC_CONFIG=true; shift ;;
        --frappe-branch) FRAPPE_BRANCH="$2"; shift 2 ;;
        --erpnext-branch) ERPNEXT_BRANCH="$2"; shift 2 ;;
        --help|-h)
            echo "Usage: sudo $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --domain DOMAIN          Your ERPNext domain (e.g., erp.example.com)"
            echo "  --email EMAIL            Admin email for Let's Encrypt and ERPNext"
            echo "  --db-password PASS       MariaDB root password"
            echo "  --admin-password PASS    ERPNext Administrator password"
            echo "  --wc-url URL             WooCommerce store URL (e.g., https://store.com)"
            echo "  --wc-key KEY             WooCommerce Consumer Key"
            echo "  --wc-secret SECRET       WooCommerce Consumer Secret"
            echo "  --wc-webhook-secret SEC  WooCommerce Webhook Secret"
            echo "  --company NAME           Company name (default: My Company)"
            echo "  --company-abbr ABBR      Company abbreviation (default: MC)"
            echo "  --country COUNTRY        Country (default: United States)"
            echo "  --currency CUR           Currency (default: USD)"
            echo "  --skip-ssl               Skip SSL/Let's Encrypt setup"
            echo "  --skip-wc                Skip WooCommerce configuration"
            echo "  --frappe-branch BRANCH   Frappe branch (default: version-15)"
            echo "  --erpnext-branch BRANCH  ERPNext branch (default: version-15)"
            echo "  --help, -h               Show this help message"
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            exit 1
            ;;
    esac
done

# ============================================================================
# Pre-flight checks
# ============================================================================
if [[ $EUID -ne 0 ]]; then
    log_error "This script must be run as root (use sudo)"
    exit 1
fi

# Check OS
if ! grep -qiE 'ubuntu (22|24)\.' /etc/os-release 2>/dev/null; then
    log_warn "This script is designed for Ubuntu 22.04/24.04. Continuing anyway..."
fi

# Check RAM
TOTAL_RAM=$(free -m | awk '/^Mem:/{print $2}')
if [[ $TOTAL_RAM -lt 1800 ]]; then
    log_error "Minimum 2 GB RAM required. Found: ${TOTAL_RAM}MB"
    log_warn "Consider adding swap space or using a larger VPS"
    exit 1
fi

# Interactive prompts for missing required values
if [[ -z "$DOMAIN" ]]; then
    read -rp "Enter your domain name (e.g., erp.example.com): " DOMAIN
fi
SITE_NAME="${DOMAIN}"

if [[ -z "$ADMIN_EMAIL" ]]; then
    read -rp "Enter admin email: " ADMIN_EMAIL
fi

if [[ -z "$DB_ROOT_PASSWORD" ]]; then
    DB_ROOT_PASSWORD=$(openssl rand -base64 24 | tr -d '/+=')
    log_warn "Generated MariaDB root password: $DB_ROOT_PASSWORD"
    log_warn "SAVE THIS PASSWORD - you will need it later!"
fi

if [[ -z "$ADMIN_PASSWORD" ]]; then
    ADMIN_PASSWORD=$(openssl rand -base64 16 | tr -d '/+=')
    log_warn "Generated ERPNext admin password: $ADMIN_PASSWORD"
    log_warn "SAVE THIS PASSWORD - you will need it later!"
fi

# Save credentials to a secure file
CREDS_FILE="/root/.erpnext_credentials"
cat > "$CREDS_FILE" << EOF
# ERPNext Deployment Credentials
# Generated: $(date -Iseconds)
# KEEP THIS FILE SECURE - DELETE AFTER NOTING CREDENTIALS

DOMAIN=${DOMAIN}
SITE_NAME=${SITE_NAME}
ADMIN_EMAIL=${ADMIN_EMAIL}
DB_ROOT_PASSWORD=${DB_ROOT_PASSWORD}
ADMIN_PASSWORD=${ADMIN_PASSWORD}
WC_URL=${WC_URL}
WC_CONSUMER_KEY=${WC_CONSUMER_KEY}
WC_CONSUMER_SECRET=${WC_CONSUMER_SECRET}
WC_WEBHOOK_SECRET=${WC_WEBHOOK_SECRET}
EOF
chmod 600 "$CREDS_FILE"
log_info "Credentials saved to $CREDS_FILE"

echo ""
echo "============================================================"
echo "  ERPNext + WooCommerce Deployment"
echo "============================================================"
echo "  Domain:    ${DOMAIN}"
echo "  Email:     ${ADMIN_EMAIL}"
echo "  Company:   ${COMPANY_NAME} (${COMPANY_ABBR})"
echo "  Country:   ${COUNTRY}"
echo "  Currency:  ${CURRENCY}"
echo "  WC Store:  ${WC_URL:-'(will configure later)'}"
echo "============================================================"
echo ""

# ============================================================================
# Step 1: System Updates & Dependencies
# ============================================================================
log_info "Step 1/12: Installing system dependencies..."

export DEBIAN_FRONTEND=noninteractive

apt-get update -qq
apt-get upgrade -y -qq

apt-get install -y -qq \
    git \
    python3-dev \
    python3-pip \
    python3-venv \
    python3-setuptools \
    software-properties-common \
    build-essential \
    libffi-dev \
    libssl-dev \
    libjpeg-dev \
    zlib1g-dev \
    libfreetype6-dev \
    liblcms2-dev \
    libwebp-dev \
    libharfbuzz-dev \
    libfribidi-dev \
    libxcb1-dev \
    redis-server \
    curl \
    wget \
    supervisor \
    nginx \
    certbot \
    python3-certbot-nginx \
    fail2ban \
    ufw \
    cron \
    xvfb \
    libfontconfig \
    > /dev/null 2>&1

log_ok "System dependencies installed"

# ============================================================================
# Step 2: Install Node.js
# ============================================================================
log_info "Step 2/12: Installing Node.js ${NODE_VERSION}..."

if ! command -v node &>/dev/null || ! node -v | grep -q "v${NODE_VERSION}"; then
    curl -fsSL https://deb.nodesource.com/setup_${NODE_VERSION}.x | bash - > /dev/null 2>&1
    apt-get install -y -qq nodejs > /dev/null 2>&1
fi

npm install -g yarn > /dev/null 2>&1

log_ok "Node.js $(node -v) installed"

# ============================================================================
# Step 3: Install & Configure MariaDB
# ============================================================================
log_info "Step 3/12: Installing and configuring MariaDB..."

apt-get install -y -qq mariadb-server mariadb-client libmysqlclient-dev > /dev/null 2>&1

# Configure MariaDB for ERPNext
cat > /etc/mysql/mariadb.conf.d/99-erpnext.cnf << 'MARIADB_CONF'
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
MARIADB_CONF

systemctl restart mariadb
systemctl enable mariadb

# Set root password
mysql -u root <<MYSQL_INIT
ALTER USER 'root'@'localhost' IDENTIFIED BY '${DB_ROOT_PASSWORD}';
FLUSH PRIVILEGES;
MYSQL_INIT

log_ok "MariaDB installed and configured"

# ============================================================================
# Step 4: Install wkhtmltopdf
# ============================================================================
log_info "Step 4/12: Installing wkhtmltopdf..."

ARCH=$(dpkg --print-architecture)
WKHTML_URL="https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6.1-3/wkhtmltox_0.12.6.1-3.jammy_${ARCH}.deb"

if ! command -v wkhtmltopdf &>/dev/null; then
    wget -q "$WKHTML_URL" -O /tmp/wkhtmltox.deb || true
    if [[ -f /tmp/wkhtmltox.deb ]]; then
        apt-get install -y -qq /tmp/wkhtmltox.deb > /dev/null 2>&1 || apt-get install -y -qq wkhtmltopdf > /dev/null 2>&1
        rm -f /tmp/wkhtmltox.deb
    else
        apt-get install -y -qq wkhtmltopdf > /dev/null 2>&1
    fi
fi

log_ok "wkhtmltopdf installed"

# ============================================================================
# Step 5: Create frappe user
# ============================================================================
log_info "Step 5/12: Creating frappe system user..."

if ! id -u "$FRAPPE_USER" &>/dev/null; then
    useradd -m -s /bin/bash "$FRAPPE_USER"
    usermod -aG sudo "$FRAPPE_USER"
    echo "${FRAPPE_USER} ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/frappe
fi

log_ok "User '$FRAPPE_USER' ready"

# ============================================================================
# Step 6: Install Frappe Bench
# ============================================================================
log_info "Step 6/12: Installing Frappe Bench..."

sudo -u "$FRAPPE_USER" pip3 install --user frappe-bench > /dev/null 2>&1 || \
    pip3 install frappe-bench > /dev/null 2>&1

# Ensure bench is in PATH
BENCH_BIN=$(sudo -u "$FRAPPE_USER" bash -c 'which bench 2>/dev/null || echo /home/frappe/.local/bin/bench')
if [[ ! -f "$BENCH_BIN" ]]; then
    BENCH_BIN="/home/${FRAPPE_USER}/.local/bin/bench"
fi

log_ok "Frappe Bench installed"

# ============================================================================
# Step 7: Initialize Bench & Install ERPNext
# ============================================================================
log_info "Step 7/12: Initializing bench and installing ERPNext (this takes a while)..."

if [[ ! -d "$BENCH_DIR" ]]; then
    sudo -u "$FRAPPE_USER" bash -c "
        export PATH=\$HOME/.local/bin:\$PATH
        cd /home/${FRAPPE_USER}
        bench init frappe-bench --frappe-branch ${FRAPPE_BRANCH} --python python${PYTHON_VERSION} 2>&1
    " | tail -5
fi

# Install ERPNext app
sudo -u "$FRAPPE_USER" bash -c "
    export PATH=\$HOME/.local/bin:\$PATH
    cd ${BENCH_DIR}
    if [[ ! -d apps/erpnext ]]; then
        bench get-app erpnext --branch ${ERPNEXT_BRANCH} 2>&1
    fi
" | tail -5

log_ok "Bench initialized with ERPNext"

# ============================================================================
# Step 8: Create Site
# ============================================================================
log_info "Step 8/12: Creating site ${SITE_NAME}..."

sudo -u "$FRAPPE_USER" bash -c "
    export PATH=\$HOME/.local/bin:\$PATH
    cd ${BENCH_DIR}

    # Create the site
    bench new-site ${SITE_NAME} \
        --db-root-password '${DB_ROOT_PASSWORD}' \
        --admin-password '${ADMIN_PASSWORD}' \
        --mariadb-root-password '${DB_ROOT_PASSWORD}' \
        --install-app erpnext \
        2>&1

    # Set as default site
    bench use ${SITE_NAME}

    # Run migrate to set up custom fields including WooCommerce
    bench --site ${SITE_NAME} migrate 2>&1
" | tail -10

log_ok "Site ${SITE_NAME} created with ERPNext"

# ============================================================================
# Step 9: Setup Production (Supervisor + Nginx)
# ============================================================================
log_info "Step 9/12: Setting up production mode..."

sudo -u "$FRAPPE_USER" bash -c "
    export PATH=\$HOME/.local/bin:\$PATH
    cd ${BENCH_DIR}
    sudo bench setup production ${FRAPPE_USER} --yes 2>&1
" | tail -5

# Tune nginx for production
cat > /etc/nginx/conf.d/erpnext-tuning.conf << 'NGINX_TUNE'
# ERPNext production tuning
client_max_body_size 50m;
proxy_read_timeout 120;
proxy_connect_timeout 120;
proxy_send_timeout 120;
NGINX_TUNE

systemctl reload nginx

log_ok "Production mode configured (supervisor + nginx)"

# ============================================================================
# Step 10: SSL Certificate
# ============================================================================
if [[ "$SKIP_SSL" = false ]] && [[ -n "$DOMAIN" ]]; then
    log_info "Step 10/12: Setting up SSL with Let's Encrypt..."

    certbot --nginx \
        -d "$DOMAIN" \
        --non-interactive \
        --agree-tos \
        --email "$ADMIN_EMAIL" \
        --redirect \
        2>&1 | tail -5

    # Auto-renew cron
    (crontab -l 2>/dev/null; echo "0 0,12 * * * certbot renew --quiet") | sort -u | crontab -

    log_ok "SSL certificate installed for ${DOMAIN}"
else
    log_warn "Step 10/12: Skipping SSL setup"
fi

# ============================================================================
# Step 11: Configure WooCommerce Integration
# ============================================================================
if [[ "$SKIP_WC_CONFIG" = false ]] && [[ -n "$WC_URL" ]]; then
    log_info "Step 11/12: Configuring WooCommerce integration..."

    sudo -u "$FRAPPE_USER" bash -c "
        export PATH=\$HOME/.local/bin:\$PATH
        cd ${BENCH_DIR}

        bench --site ${SITE_NAME} console <<PYEOF
import frappe

# Configure WooCommerce Settings
settings = frappe.get_single('WooCommerce Settings')
settings.enabled = 1
settings.woocommerce_url = '${WC_URL}'
settings.consumer_key = '${WC_CONSUMER_KEY}'
settings.consumer_secret = '${WC_CONSUMER_SECRET}'
settings.webhook_secret = '${WC_WEBHOOK_SECRET}' if '${WC_WEBHOOK_SECRET}' else None
settings.verify_ssl = 1
settings.sync_products = 1
settings.sync_orders = 1
settings.sync_stock = 1
settings.product_sync_direction = 'Bidirectional'
settings.stock_sync_direction = 'ERPNext to WooCommerce'
settings.order_status_filter = 'processing,completed'
settings.sync_frequency = 'Every 15 minutes'

# Set defaults - find existing or use defaults
company = frappe.db.get_single_value('Global Defaults', 'default_company') or frappe.get_all('Company', limit=1)[0].name
settings.company = company
settings.warehouse = frappe.db.get_value('Warehouse', {'company': company, 'is_group': 0}, 'name') or 'Stores - ${COMPANY_ABBR}'
settings.price_list = 'Standard Selling'
settings.default_customer_group = frappe.db.get_single_value('Selling Settings', 'customer_group') or 'All Customer Groups'
settings.default_item_group = 'All Item Groups'

# Set tax and shipping accounts if available
tax_account = frappe.db.get_value('Account', {'company': company, 'account_type': 'Tax', 'is_group': 0}, 'name')
if tax_account:
    settings.tax_account = tax_account

expense_account = frappe.db.get_value('Account', {'company': company, 'root_type': 'Expense', 'is_group': 0, 'account_name': ['like', '%Shipping%']}, 'name')
if expense_account:
    settings.shipping_account = expense_account

# Enable POS
settings.enable_pos = 1

settings.flags.ignore_permissions = True
settings.flags.ignore_mandatory = True
settings.save()

# Set up POS
from erpnext.erpnext_integrations.doctype.woocommerce_settings.pos_setup import setup_pos_for_woocommerce
setup_pos_for_woocommerce(settings)

# Set up custom fields
from erpnext.erpnext_integrations.woocommerce_custom_fields import setup_custom_fields
setup_custom_fields()

frappe.db.commit()
print('WooCommerce integration configured successfully!')
print(f'Company: {company}')
print(f'Warehouse: {settings.warehouse}')
print(f'POS Profile: {settings.pos_profile}')
PYEOF
    " 2>&1 | tail -10

    log_ok "WooCommerce integration configured"
else
    log_warn "Step 11/12: Skipping WooCommerce configuration (provide --wc-url to configure)"
fi

# ============================================================================
# Step 12: Security & Backup Configuration
# ============================================================================
log_info "Step 12/12: Configuring security and backups..."

# --- Firewall ---
ufw --force enable > /dev/null 2>&1
ufw default deny incoming > /dev/null 2>&1
ufw default allow outgoing > /dev/null 2>&1
ufw allow ssh > /dev/null 2>&1
ufw allow 80/tcp > /dev/null 2>&1
ufw allow 443/tcp > /dev/null 2>&1
# Allow socketio
ufw allow 9000/tcp > /dev/null 2>&1

# --- Fail2ban ---
cat > /etc/fail2ban/jail.local << 'FAIL2BAN'
[DEFAULT]
bantime = 3600
findtime = 600
maxretry = 5

[sshd]
enabled = true

[nginx-http-auth]
enabled = true
FAIL2BAN
systemctl restart fail2ban

# --- Automated backups ---
sudo -u "$FRAPPE_USER" bash -c "
    export PATH=\$HOME/.local/bin:\$PATH
    cd ${BENCH_DIR}
    bench --site ${SITE_NAME} enable-scheduler 2>&1
" | tail -3

# Add daily backup cron
BACKUP_CRON="0 2 * * * cd ${BENCH_DIR} && /home/${FRAPPE_USER}/.local/bin/bench --site ${SITE_NAME} backup --with-files > /dev/null 2>&1"
(sudo -u "$FRAPPE_USER" crontab -l 2>/dev/null; echo "$BACKUP_CRON") | sort -u | sudo -u "$FRAPPE_USER" crontab -

# --- Health check script ---
cat > /usr/local/bin/erpnext-health-check.sh << 'HEALTHCHECK'
#!/bin/bash
# ERPNext Health Check
SITE_URL="http://localhost"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$SITE_URL/api/method/frappe.client.get_count?doctype=DocType" --max-time 10)

if [[ "$STATUS" -ne 200 ]]; then
    echo "$(date): ERPNext health check FAILED (HTTP $STATUS)" >> /var/log/erpnext-health.log
    # Restart services
    sudo supervisorctl restart all
    echo "$(date): Services restarted" >> /var/log/erpnext-health.log
else
    echo "$(date): ERPNext health check OK" >> /var/log/erpnext-health.log
fi
HEALTHCHECK
chmod +x /usr/local/bin/erpnext-health-check.sh

# Health check every 5 minutes
(crontab -l 2>/dev/null; echo "*/5 * * * * /usr/local/bin/erpnext-health-check.sh") | sort -u | crontab -

# --- Swap space (for low-RAM VPS) ---
if [[ $TOTAL_RAM -lt 4000 ]] && [[ ! -f /swapfile ]]; then
    log_info "Adding 2GB swap space for low-RAM VPS..."
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile > /dev/null
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# --- Log rotation ---
cat > /etc/logrotate.d/erpnext << LOGROTATE
${BENCH_DIR}/logs/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
LOGROTATE

log_ok "Security and backup configured"

# ============================================================================
# Final Summary
# ============================================================================
WEBHOOK_URL="https://${DOMAIN}/api/method/erpnext.erpnext_integrations.connectors.woocommerce_webhook.handle_webhook"

echo ""
echo "============================================================"
echo -e "${GREEN}  DEPLOYMENT COMPLETE!${NC}"
echo "============================================================"
echo ""
echo "  ERPNext URL:     https://${DOMAIN}"
echo "  Admin Login:     Administrator"
echo "  Admin Password:  ${ADMIN_PASSWORD}"
echo ""
if [[ -n "$WC_URL" ]]; then
echo "  WooCommerce Integration: CONFIGURED"
echo "  WC Store:        ${WC_URL}"
echo ""
echo "  Webhook URL (add to WooCommerce):"
echo "    ${WEBHOOK_URL}"
echo ""
echo "  Webhook Topics to configure in WooCommerce:"
echo "    - Order created"
echo "    - Order updated"
echo "    - Product created"
echo "    - Product updated"
echo ""
fi
echo "  Credentials:     ${CREDS_FILE}"
echo "  Bench Dir:       ${BENCH_DIR}"
echo "  Logs:            ${BENCH_DIR}/logs/"
echo "  Backups:         Daily at 2 AM"
echo ""
echo "============================================================"
echo "  POST-DEPLOYMENT STEPS:"
echo "============================================================"
echo ""
echo "  1. Log into ERPNext at https://${DOMAIN}"
echo "     Run the Setup Wizard to configure your company."
echo ""
echo "  2. Go to WooCommerce Settings:"
echo "     https://${DOMAIN}/app/woocommerce-settings"
echo "     Click 'Test Connection' to verify."
echo ""
echo "  3. In your WooCommerce store (WordPress Admin):"
echo "     - Go to WooCommerce > Settings > Advanced > Webhooks"
echo "     - Add webhook for 'Order created' → ${WEBHOOK_URL}"
echo "     - Add webhook for 'Order updated' → ${WEBHOOK_URL}"
echo "     - Add webhook for 'Product updated' → ${WEBHOOK_URL}"
echo "     - Set the Secret to match your webhook_secret"
echo ""
echo "  4. Initial product sync:"
echo "     In WooCommerce Settings, click Sync > Full Sync"
echo ""
echo "  5. POS is ready at:"
echo "     https://${DOMAIN}/app/pos"
echo ""
echo "  Useful commands:"
echo "    sudo -u ${FRAPPE_USER} bash -c 'cd ${BENCH_DIR} && bench --site ${SITE_NAME} console'"
echo "    sudo -u ${FRAPPE_USER} bash -c 'cd ${BENCH_DIR} && bench --site ${SITE_NAME} backup --with-files'"
echo "    sudo supervisorctl status"
echo "    sudo supervisorctl restart all"
echo ""
echo "============================================================"
echo -e "${YELLOW}  IMPORTANT: Save your credentials from ${CREDS_FILE}${NC}"
echo -e "${YELLOW}  Then: sudo rm ${CREDS_FILE}${NC}"
echo "============================================================"
