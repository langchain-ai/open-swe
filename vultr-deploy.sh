#!/bin/bash

# Vultr Backend Deployment Script for Open SWE
# This script automates the deployment of the agent-mojo backend on a Vultr VPS

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
APP_USER="openswe"
APP_DIR="/opt/open-swe"
LOG_DIR="/var/log/open-swe"
NGINX_SITE="open-swe"
APP_PORT="3001"

# Function to print colored output
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

# Function to check if running as root
check_root() {
    if [[ $EUID -ne 0 ]]; then
        print_error "This script must be run as root"
        exit 1
    fi
}

# Function to update system
update_system() {
    print_status "Updating system packages..."
    apt update && apt upgrade -y
    print_success "System updated successfully"
}

# Function to install Node.js
install_nodejs() {
    print_status "Installing Node.js 18..."
    curl -fsSL https://deb.nodesource.com/setup_18.x | bash -
    apt-get install -y nodejs
    
    # Verify installation
    node_version=$(node --version)
    npm_version=$(npm --version)
    print_success "Node.js $node_version and npm $npm_version installed"
}

# Function to install Yarn
install_yarn() {
    print_status "Installing Yarn package manager..."
    npm install -g yarn
    yarn_version=$(yarn --version)
    print_success "Yarn $yarn_version installed"
}

# Function to install PM2
install_pm2() {
    print_status "Installing PM2 process manager..."
    npm install -g pm2
    pm2_version=$(pm2 --version)
    print_success "PM2 $pm2_version installed"
}

# Function to install Nginx
install_nginx() {
    print_status "Installing Nginx..."
    apt install -y nginx
    systemctl enable nginx
    systemctl start nginx
    print_success "Nginx installed and started"
}

# Function to create application user
create_app_user() {
    print_status "Creating application user: $APP_USER"
    if id "$APP_USER" &>/dev/null; then
        print_warning "User $APP_USER already exists"
    else
        adduser --system --group --home $APP_DIR $APP_USER
        print_success "User $APP_USER created"
    fi
}

# Function to setup application directory
setup_app_directory() {
    print_status "Setting up application directory..."
    
    if [ ! -d "$APP_DIR" ]; then
        mkdir -p $APP_DIR
    fi
    
    # Create log directory
    mkdir -p $LOG_DIR
    chown -R $APP_USER:$APP_USER $LOG_DIR
    
    print_success "Application directory setup complete"
}

# Function to clone repository
clone_repository() {
    print_status "Cloning repository..."
    
    if [ -z "$REPO_URL" ]; then
        print_error "REPO_URL environment variable not set"
        print_error "Please set it with: export REPO_URL=https://github.com/your-username/open-swe.git"
        exit 1
    fi
    
    # Remove existing directory if it exists
    if [ -d "$APP_DIR/.git" ]; then
        print_warning "Repository already exists, pulling latest changes..."
        cd $APP_DIR
        sudo -u $APP_USER git pull
    else
        sudo -u $APP_USER git clone $REPO_URL $APP_DIR
    fi
    
    chown -R $APP_USER:$APP_USER $APP_DIR
    print_success "Repository cloned successfully"
}

# Function to install dependencies and build
build_application() {
    print_status "Installing dependencies and building application..."
    
    cd $APP_DIR
    sudo -u $APP_USER yarn install
    
    cd $APP_DIR/apps/agent-mojo
    sudo -u $APP_USER yarn build
    
    print_success "Application built successfully"
}

# Function to create environment file template
create_env_template() {
    print_status "Creating environment file template..."
    
    cat > $APP_DIR/apps/agent-mojo/.env.production << 'EOF'
# Server Configuration
NODE_ENV=production
PORT=3001

# LLM Provider Keys (REQUIRED - Replace with your actual keys)
OPENAI_API_KEY=your-openai-api-key-here
ANTHROPIC_API_KEY=your-anthropic-api-key-here
GOOGLE_API_KEY=your-google-api-key-here

# Infrastructure (REQUIRED - Replace with your actual keys)
DAYTONA_API_KEY=your-daytona-api-key-here
FIRECRAWL_API_KEY=your-firecrawl-api-key-here

# GitHub App Configuration (REQUIRED - Replace with your actual values)
GITHUB_APP_NAME=Ageent Mojo
GITHUB_APP_ID=1806822
GITHUB_APP_PRIVATE_KEY=your-github-app-private-key-here
GITHUB_WEBHOOK_SECRET=your-github-webhook-secret-here
GITHUB_APP_CLIENT_SECRET=your-github-app-client-secret-here
GITHUB_APP_REDIRECT_URI=https://your-vercel-app.vercel.app/api/auth/github/callback

# Other Configuration (REQUIRED - Replace with your actual values)
OPEN_SWE_APP_URL=https://your-vercel-app.vercel.app
SECRETS_ENCRYPTION_KEY=your-32-byte-hex-encryption-key-here
SKIP_CI_UNTIL_LAST_COMMIT=true
OPEN_SWE_LOCAL_MODE=false
EOF

    chown $APP_USER:$APP_USER $APP_DIR/apps/agent-mojo/.env.production
    print_success "Environment file template created at $APP_DIR/apps/agent-mojo/.env.production"
    print_warning "IMPORTANT: Edit this file with your actual API keys and configuration!"
}

# Function to create PM2 ecosystem file
create_pm2_config() {
    print_status "Creating PM2 ecosystem configuration..."
    
    cat > $APP_DIR/ecosystem.config.js << EOF
module.exports = {
  apps: [{
    name: 'open-swe-agent',
    cwd: '$APP_DIR/apps/agent-mojo',
    script: 'yarn',
    args: 'start',
    env: {
      NODE_ENV: 'production',
      PORT: $APP_PORT
    },
    env_file: '.env.production',
    instances: 1,
    exec_mode: 'fork',
    watch: false,
    max_memory_restart: '1G',
    error_file: '$LOG_DIR/error.log',
    out_file: '$LOG_DIR/out.log',
    log_file: '$LOG_DIR/combined.log',
    time: true,
    restart_delay: 4000,
    max_restarts: 10,
    min_uptime: '10s'
  }]
};
EOF

    chown $APP_USER:$APP_USER $APP_DIR/ecosystem.config.js
    print_success "PM2 ecosystem configuration created"
}

# Function to configure Nginx
configure_nginx() {
    print_status "Configuring Nginx..."
    
    # Get server IP or domain
    if [ -z "$SERVER_DOMAIN" ]; then
        SERVER_DOMAIN=$(curl -s ifconfig.me)
        print_warning "SERVER_DOMAIN not set, using server IP: $SERVER_DOMAIN"
    fi
    
    cat > /etc/nginx/sites-available/$NGINX_SITE << EOF
server {
    listen 80;
    server_name $SERVER_DOMAIN;
    
    # Security headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "no-referrer-when-downgrade" always;
    add_header Content-Security-Policy "default-src 'self' http: https: data: blob: 'unsafe-inline'" always;
    
    # Rate limiting
    limit_req_zone \$binary_remote_addr zone=api:10m rate=10r/s;
    
    location / {
        limit_req zone=api burst=20 nodelay;
        
        proxy_pass http://localhost:$APP_PORT;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_cache_bypass \$http_upgrade;
        proxy_read_timeout 300s;
        proxy_connect_timeout 75s;
        
        # CORS headers for API requests
        add_header Access-Control-Allow-Origin "https://your-vercel-app.vercel.app" always;
        add_header Access-Control-Allow-Methods "GET, POST, PUT, DELETE, OPTIONS" always;
        add_header Access-Control-Allow-Headers "Authorization, Content-Type, Accept" always;
        add_header Access-Control-Allow-Credentials "true" always;
        
        if (\$request_method = 'OPTIONS') {
            return 204;
        }
    }
    
    # Health check endpoint
    location /health {
        proxy_pass http://localhost:$APP_PORT/health;
        access_log off;
    }
}
EOF

    # Enable the site
    ln -sf /etc/nginx/sites-available/$NGINX_SITE /etc/nginx/sites-enabled/
    
    # Remove default site if it exists
    if [ -f "/etc/nginx/sites-enabled/default" ]; then
        rm /etc/nginx/sites-enabled/default
    fi
    
    # Test Nginx configuration
    nginx -t
    systemctl restart nginx
    
    print_success "Nginx configured and restarted"
}

# Function to setup firewall
setup_firewall() {
    print_status "Setting up firewall..."
    
    # Install ufw if not present
    apt install -y ufw
    
    # Reset firewall rules
    ufw --force reset
    
    # Default policies
    ufw default deny incoming
    ufw default allow outgoing
    
    # Allow SSH
    ufw allow ssh
    
    # Allow HTTP and HTTPS
    ufw allow 80/tcp
    ufw allow 443/tcp
    
    # Enable firewall
    ufw --force enable
    
    print_success "Firewall configured"
}

# Function to install SSL certificate
install_ssl() {
    if [ -z "$SERVER_DOMAIN" ] || [[ $SERVER_DOMAIN =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        print_warning "Skipping SSL installation - no domain name provided or using IP address"
        return
    fi
    
    print_status "Installing SSL certificate for $SERVER_DOMAIN..."
    
    # Install certbot
    apt install -y certbot python3-certbot-nginx
    
    # Obtain certificate
    certbot --nginx -d $SERVER_DOMAIN --non-interactive --agree-tos --email admin@$SERVER_DOMAIN
    
    # Setup auto-renewal
    systemctl enable certbot.timer
    
    print_success "SSL certificate installed and auto-renewal configured"
}

# Function to start application
start_application() {
    print_status "Starting application with PM2..."
    
    cd $APP_DIR
    sudo -u $APP_USER pm2 start ecosystem.config.js
    sudo -u $APP_USER pm2 save
    
    # Setup PM2 startup script
    env_path=$(sudo -u $APP_USER pm2 startup | grep 'sudo env' | cut -d' ' -f3-)
    if [ ! -z "$env_path" ]; then
        eval $env_path
    fi
    
    print_success "Application started successfully"
}

# Function to display final instructions
show_final_instructions() {
    print_success "\n=== Deployment Complete! ==="
    echo -e "\n${BLUE}Next Steps:${NC}"
    echo -e "1. Edit the environment file: ${YELLOW}$APP_DIR/apps/agent-mojo/.env.production${NC}"
    echo -e "2. Add your actual API keys and configuration values"
    echo -e "3. Restart the application: ${YELLOW}sudo -u $APP_USER pm2 restart open-swe-agent${NC}"
    echo -e "\n${BLUE}Useful Commands:${NC}"
    echo -e "• Check application status: ${YELLOW}sudo -u $APP_USER pm2 status${NC}"
    echo -e "• View application logs: ${YELLOW}sudo -u $APP_USER pm2 logs${NC}"
    echo -e "• Restart application: ${YELLOW}sudo -u $APP_USER pm2 restart open-swe-agent${NC}"
    echo -e "• Check Nginx status: ${YELLOW}systemctl status nginx${NC}"
    echo -e "• Test API endpoint: ${YELLOW}curl http://$SERVER_DOMAIN/health${NC}"
    echo -e "\n${BLUE}Your backend is accessible at:${NC} ${GREEN}http://$SERVER_DOMAIN${NC}"
    
    if [ ! -z "$SERVER_DOMAIN" ] && [[ ! $SERVER_DOMAIN =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        echo -e "${BLUE}SSL URL:${NC} ${GREEN}https://$SERVER_DOMAIN${NC}"
    fi
    
    echo -e "\n${YELLOW}Remember to update your Vercel environment variables with the backend URL!${NC}"
}

# Main deployment function
main() {
    print_status "Starting Open SWE Backend Deployment on Vultr..."
    
    check_root
    update_system
    install_nodejs
    install_yarn
    install_pm2
    install_nginx
    create_app_user
    setup_app_directory
    clone_repository
    build_application
    create_env_template
    create_pm2_config
    configure_nginx
    setup_firewall
    install_ssl
    start_application
    show_final_instructions
}

# Script usage
usage() {
    echo "Usage: $0"
    echo "Environment variables:"
    echo "  REPO_URL - Git repository URL (required)"
    echo "  SERVER_DOMAIN - Your domain name (optional, uses server IP if not set)"
    echo ""
    echo "Example:"
    echo "  export REPO_URL=https://github.com/your-username/open-swe.git"
    echo "  export SERVER_DOMAIN=api.yourdomain.com"
    echo "  bash vultr-deploy.sh"
}

# Check if help is requested
if [[ "$1" == "-h" ]] || [[ "$1" == "--help" ]]; then
    usage
    exit 0
fi

# Run main function
main "$@"