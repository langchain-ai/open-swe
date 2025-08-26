#!/bin/bash

# Vultr Full-Stack Deployment Script for Open-SWE Monorepo
# Deploys both Next.js web app and Agent-Mojo backend on a single server
# Author: AI Assistant
# Version: 1.0

set -e  # Exit on any error

# Configuration
APP_USER="openswe"
APP_DIR="/home/$APP_USER/open-swe"
LOG_DIR="/var/log/openswe"
NGINX_SITE="openswe"
WEB_PORT=3000
API_PORT=8000
NODE_VERSION="18"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Print status messages
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if running as root
check_root() {
    if [[ $EUID -ne 0 ]]; then
        print_error "This script must be run as root (use sudo)"
        exit 1
    fi
}

# Update system packages
update_system() {
    print_status "Updating system packages..."
    apt update && apt upgrade -y
    apt install -y curl wget git build-essential software-properties-common ufw nginx certbot python3-certbot-nginx
    print_success "System packages updated"
}

# Install Node.js
install_nodejs() {
    print_status "Installing Node.js $NODE_VERSION..."
    curl -fsSL https://deb.nodesource.com/setup_${NODE_VERSION}.x | bash -
    apt-get install -y nodejs
    
    # Verify installation
    node_version=$(node --version)
    npm_version=$(npm --version)
    print_success "Node.js $node_version and npm $npm_version installed"
}

# Install Yarn
install_yarn() {
    print_status "Installing Yarn package manager..."
    npm install -g yarn
    yarn_version=$(yarn --version)
    print_success "Yarn $yarn_version installed"
}

# Install PM2
install_pm2() {
    print_status "Installing PM2 process manager..."
    npm install -g pm2
    pm2_version=$(pm2 --version)
    print_success "PM2 $pm2_version installed"
}

# Create application user
create_app_user() {
    print_status "Creating application user: $APP_USER"
    if id "$APP_USER" &>/dev/null; then
        print_warning "User $APP_USER already exists"
    else
        useradd -m -s /bin/bash "$APP_USER"
        usermod -aG sudo "$APP_USER"
        print_success "User $APP_USER created"
    fi
}

# Setup application directory
setup_app_directory() {
    print_status "Setting up application directory..."
    
    # Create log directory
    mkdir -p "$LOG_DIR"
    chown "$APP_USER:$APP_USER" "$LOG_DIR"
    
    # Switch to app user for remaining operations
    sudo -u "$APP_USER" bash << EOF
        cd /home/$APP_USER
        
        # Remove existing directory if it exists
        if [ -d "open-swe" ]; then
            rm -rf open-swe
        fi
EOF
    
    print_success "Application directory setup complete"
}

# Clone repository
clone_repository() {
    print_status "Cloning repository..."
    
    if [ -z "$REPO_URL" ]; then
        print_error "REPO_URL environment variable is required"
        print_error "Usage: REPO_URL=<your-repo-url> SERVER_DOMAIN=<your-domain> ./vultr-full-stack-deploy.sh"
        exit 1
    fi
    
    sudo -u "$APP_USER" bash << EOF
        cd /home/$APP_USER
        git clone "$REPO_URL" open-swe
        cd open-swe
        git checkout main || git checkout master
EOF
    
    print_success "Repository cloned successfully"
}

# Build applications
build_applications() {
    print_status "Building applications..."
    
    sudo -u "$APP_USER" bash << EOF
        cd "$APP_DIR"
        
        # Install dependencies
        print_status "Installing dependencies..."
        yarn install
        
        # Build web app
        print_status "Building Next.js web application..."
        cd apps/web
        yarn build
        cd ../..
        
        # Build agent-mojo
        print_status "Building Agent-Mojo backend..."
        cd apps/agent-mojo
        yarn build
        cd ../..
EOF
    
    print_success "Applications built successfully"
}

# Create environment files
create_environment_files() {
    print_status "Creating environment configuration files..."
    
    # Web app environment
    sudo -u "$APP_USER" tee "$APP_DIR/apps/web/.env.production" > /dev/null << 'EOF'
# Next.js Web App Production Environment
NODE_ENV=production
NEXT_PUBLIC_API_URL=https://YOUR_DOMAIN/api
NEXT_PUBLIC_WS_URL=wss://YOUR_DOMAIN/ws

# Authentication
NEXTAUTH_URL=https://YOUR_DOMAIN
NEXTAUTH_SECRET=your-nextauth-secret-here

# GitHub OAuth (if using)
GITHUB_CLIENT_ID=your-github-client-id
GITHUB_CLIENT_SECRET=your-github-client-secret

# Database (if needed by web app)
DATABASE_URL=postgresql://user:password@localhost:5432/openswe

# Redis (if using for sessions)
REDIS_URL=redis://localhost:6379
EOF

    # Agent-Mojo environment
    sudo -u "$APP_USER" tee "$APP_DIR/apps/agent-mojo/.env.production" > /dev/null << 'EOF'
# Agent-Mojo Backend Production Environment
NODE_ENV=production
PORT=8000
HOST=0.0.0.0

# API Keys (REPLACE WITH ACTUAL VALUES)
OPENAI_API_KEY=your-openai-api-key-here
ANTHROPIC_API_KEY=your-anthropic-api-key-here
GITHUB_TOKEN=your-github-token-here
DAYTONA_API_KEY=your-daytona-api-key-here
FIRECRAWL_API_KEY=your-firecrawl-api-key-here

# Database
DATABASE_URL=postgresql://user:password@localhost:5432/openswe

# Redis
REDIS_URL=redis://localhost:6379

# Security
JWT_SECRET=your-jwt-secret-here
CORS_ORIGIN=https://YOUR_DOMAIN

# Logging
LOG_LEVEL=info
LOG_FILE=$LOG_DIR/agent-mojo.log

# Rate Limiting
RATE_LIMIT_WINDOW_MS=900000
RATE_LIMIT_MAX_REQUESTS=100
EOF

    # Replace YOUR_DOMAIN placeholder
    if [ -n "$SERVER_DOMAIN" ]; then
        sed -i "s/YOUR_DOMAIN/$SERVER_DOMAIN/g" "$APP_DIR/apps/web/.env.production"
        sed -i "s/YOUR_DOMAIN/$SERVER_DOMAIN/g" "$APP_DIR/apps/agent-mojo/.env.production"
    fi
    
    print_success "Environment files created"
    print_warning "IMPORTANT: Edit the .env.production files to add your actual API keys and secrets"
}

# Create PM2 ecosystem file
create_pm2_config() {
    print_status "Creating PM2 ecosystem configuration..."
    
    sudo -u "$APP_USER" tee "$APP_DIR/ecosystem.config.js" > /dev/null << EOF
module.exports = {
  apps: [
    {
      name: 'openswe-web',
      cwd: '$APP_DIR/apps/web',
      script: 'yarn',
      args: 'start',
      env: {
        NODE_ENV: 'production',
        PORT: $WEB_PORT
      },
      instances: 1,
      exec_mode: 'fork',
      max_memory_restart: '1G',
      error_file: '$LOG_DIR/web-error.log',
      out_file: '$LOG_DIR/web-out.log',
      log_file: '$LOG_DIR/web-combined.log',
      time: true,
      autorestart: true,
      watch: false,
      max_restarts: 10,
      min_uptime: '10s'
    },
    {
      name: 'openswe-api',
      cwd: '$APP_DIR/apps/agent-mojo',
      script: 'yarn',
      args: 'start',
      env: {
        NODE_ENV: 'production',
        PORT: $API_PORT
      },
      instances: 1,
      exec_mode: 'fork',
      max_memory_restart: '2G',
      error_file: '$LOG_DIR/api-error.log',
      out_file: '$LOG_DIR/api-out.log',
      log_file: '$LOG_DIR/api-combined.log',
      time: true,
      autorestart: true,
      watch: false,
      max_restarts: 10,
      min_uptime: '10s'
    }
  ]
};
EOF
    
    print_success "PM2 ecosystem configuration created"
}

# Configure Nginx
configure_nginx() {
    print_status "Configuring Nginx..."
    
    # Remove default site
    rm -f /etc/nginx/sites-enabled/default
    
    # Create Nginx configuration
    tee "/etc/nginx/sites-available/$NGINX_SITE" > /dev/null << EOF
# Rate limiting
limit_req_zone \$binary_remote_addr zone=api_limit:10m rate=10r/s;
limit_req_zone \$binary_remote_addr zone=web_limit:10m rate=30r/s;

# Upstream servers
upstream web_backend {
    server 127.0.0.1:$WEB_PORT;
    keepalive 32;
}

upstream api_backend {
    server 127.0.0.1:$API_PORT;
    keepalive 32;
}

server {
    listen 80;
    server_name $SERVER_DOMAIN www.$SERVER_DOMAIN;
    
    # Security headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Content-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; font-src 'self' data:; connect-src 'self' wss: https:;" always;
    
    # Gzip compression
    gzip on;
    gzip_vary on;
    gzip_min_length 1024;
    gzip_proxied any;
    gzip_comp_level 6;
    gzip_types
        text/plain
        text/css
        text/xml
        text/javascript
        application/json
        application/javascript
        application/xml+rss
        application/atom+xml
        image/svg+xml;
    
    # API routes
    location /api/ {
        limit_req zone=api_limit burst=20 nodelay;
        
        proxy_pass http://api_backend;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_cache_bypass \$http_upgrade;
        
        # CORS headers
        add_header Access-Control-Allow-Origin "https://$SERVER_DOMAIN" always;
        add_header Access-Control-Allow-Methods "GET, POST, PUT, DELETE, OPTIONS" always;
        add_header Access-Control-Allow-Headers "Authorization, Content-Type, Accept, Origin, X-Requested-With" always;
        add_header Access-Control-Allow-Credentials "true" always;
        
        # Handle preflight requests
        if (\$request_method = 'OPTIONS') {
            add_header Access-Control-Allow-Origin "https://$SERVER_DOMAIN";
            add_header Access-Control-Allow-Methods "GET, POST, PUT, DELETE, OPTIONS";
            add_header Access-Control-Allow-Headers "Authorization, Content-Type, Accept, Origin, X-Requested-With";
            add_header Access-Control-Allow-Credentials "true";
            add_header Content-Length 0;
            add_header Content-Type text/plain;
            return 204;
        }
    }
    
    # WebSocket routes
    location /ws/ {
        proxy_pass http://api_backend;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 86400;
    }
    
    # Health check endpoint
    location /health {
        access_log off;
        return 200 "healthy\n";
        add_header Content-Type text/plain;
    }
    
    # Next.js static files and pages
    location / {
        limit_req zone=web_limit burst=50 nodelay;
        
        proxy_pass http://web_backend;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_cache_bypass \$http_upgrade;
        
        # Cache static assets
        location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2|ttf|eot)\$ {
            expires 1y;
            add_header Cache-Control "public, immutable";
            access_log off;
        }
    }
    
    # Security: Block access to sensitive files
    location ~ /\. {
        deny all;
        access_log off;
        log_not_found off;
    }
    
    location ~ \.(env|log|conf)\$ {
        deny all;
        access_log off;
        log_not_found off;
    }
}
EOF
    
    # Enable the site
    ln -sf "/etc/nginx/sites-available/$NGINX_SITE" "/etc/nginx/sites-enabled/$NGINX_SITE"
    
    # Test Nginx configuration
    nginx -t
    
    # Restart Nginx
    systemctl restart nginx
    systemctl enable nginx
    
    print_success "Nginx configured and restarted"
}

# Setup firewall
setup_firewall() {
    print_status "Configuring UFW firewall..."
    
    ufw --force reset
    ufw default deny incoming
    ufw default allow outgoing
    
    # Allow SSH, HTTP, and HTTPS
    ufw allow ssh
    ufw allow 80/tcp
    ufw allow 443/tcp
    
    # Enable firewall
    ufw --force enable
    
    print_success "Firewall configured"
}

# Install SSL certificate
install_ssl() {
    if [ -z "$SERVER_DOMAIN" ]; then
        print_warning "SERVER_DOMAIN not provided, skipping SSL certificate installation"
        print_warning "You can install SSL later with: certbot --nginx -d your-domain.com"
        return
    fi
    
    print_status "Installing SSL certificate for $SERVER_DOMAIN..."
    
    # Install certificate
    certbot --nginx -d "$SERVER_DOMAIN" -d "www.$SERVER_DOMAIN" --non-interactive --agree-tos --email "admin@$SERVER_DOMAIN" --redirect
    
    # Setup auto-renewal
    systemctl enable certbot.timer
    systemctl start certbot.timer
    
    print_success "SSL certificate installed and auto-renewal configured"
}

# Start applications
start_applications() {
    print_status "Starting applications with PM2..."
    
    sudo -u "$APP_USER" bash << EOF
        cd "$APP_DIR"
        
        # Start applications
        pm2 start ecosystem.config.js
        
        # Save PM2 configuration
        pm2 save
        
        # Setup PM2 startup script
        pm2 startup systemd -u $APP_USER --hp /home/$APP_USER
EOF
    
    # Enable PM2 startup
    env PATH=\$PATH:/usr/bin /usr/lib/node_modules/pm2/bin/pm2 startup systemd -u "$APP_USER" --hp "/home/$APP_USER"
    
    print_success "Applications started with PM2"
}

# Show final instructions
show_final_instructions() {
    print_success "\n=== DEPLOYMENT COMPLETED SUCCESSFULLY ==="
    
    echo -e "\n${BLUE}Next Steps:${NC}"
    echo "1. Edit environment files with your actual API keys:"
    echo "   - $APP_DIR/apps/web/.env.production"
    echo "   - $APP_DIR/apps/agent-mojo/.env.production"
    echo ""
    echo "2. Restart applications after updating environment:"
    echo "   sudo -u $APP_USER pm2 restart all"
    echo ""
    echo "3. Your application should be accessible at:"
    if [ -n "$SERVER_DOMAIN" ]; then
        echo "   - https://$SERVER_DOMAIN (with SSL)"
        echo "   - http://$SERVER_DOMAIN (redirects to HTTPS)"
    else
        echo "   - http://YOUR_SERVER_IP"
    fi
    echo ""
    echo -e "${BLUE}Useful Commands:${NC}"
    echo "- Check application status: sudo -u $APP_USER pm2 status"
    echo "- View logs: sudo -u $APP_USER pm2 logs"
    echo "- Restart apps: sudo -u $APP_USER pm2 restart all"
    echo "- Check Nginx status: systemctl status nginx"
    echo "- Check Nginx logs: tail -f /var/log/nginx/error.log"
    echo "- Test Nginx config: nginx -t"
    echo ""
    echo -e "${YELLOW}Important Security Notes:${NC}"
    echo "- Update all API keys in the .env.production files"
    echo "- Consider setting up a database (PostgreSQL) and Redis"
    echo "- Monitor logs regularly: tail -f $LOG_DIR/*.log"
    echo "- Keep the system updated: apt update && apt upgrade"
}

# Main deployment function
main() {
    print_status "Starting Open-SWE Full-Stack Deployment on Vultr..."
    
    check_root
    update_system
    install_nodejs
    install_yarn
    install_pm2
    create_app_user
    setup_app_directory
    clone_repository
    build_applications
    create_environment_files
    create_pm2_config
    configure_nginx
    setup_firewall
    install_ssl
    start_applications
    show_final_instructions
    
    print_success "\nDeployment completed! 🚀"
}

# Usage information
usage() {
    echo "Usage: REPO_URL=<repository-url> SERVER_DOMAIN=<domain> ./vultr-full-stack-deploy.sh"
    echo ""
    echo "Environment Variables:"
    echo "  REPO_URL      - Git repository URL (required)"
    echo "  SERVER_DOMAIN - Your server domain name (optional, for SSL)"
    echo ""
    echo "Example:"
    echo "  REPO_URL=https://github.com/yourusername/open-swe.git SERVER_DOMAIN=yourdomain.com ./vultr-full-stack-deploy.sh"
    exit 1
}

# Check if help is requested
if [[ "$1" == "-h" ]] || [[ "$1" == "--help" ]]; then
    usage
fi

# Run main function
main "$@"