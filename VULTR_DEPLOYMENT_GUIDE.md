# Complete Vultr Deployment Guide for Open-SWE

This guide provides step-by-step instructions for deploying the complete Open-SWE monorepo (Next.js web app + Agent-Mojo backend) on a single Vultr server.

## 🎯 Overview

This deployment strategy offers several advantages:
- **Simplified Architecture**: Single server management
- **Cost Effective**: ~$24/month for production-ready setup
- **Easy Configuration**: Unified environment and logging
- **Better Performance**: No network latency between frontend and backend
- **Unified SSL**: Single domain with automatic HTTPS

## 📋 Prerequisites

### Required Information
- [ ] Git repository URL (your fork of Open-SWE)
- [ ] Domain name (optional but recommended for SSL)
- [ ] API keys for:
  - [ ] OpenAI API key
  - [ ] Anthropic API key (optional)
  - [ ] GitHub token
  - [ ] Daytona API key
  - [ ] Firecrawl API key

### Local Requirements
- SSH client
- Domain registrar access (if using custom domain)

## 🚀 Step 1: Create Vultr Server

### 1.1 Server Configuration
1. **Login to Vultr Dashboard**
2. **Click "Deploy New Server"**
3. **Choose Server Type**: Regular Performance
4. **Select Location**: Choose closest to your users (see [VULTR_HARDWARE_SPECS.md](./VULTR_HARDWARE_SPECS.md))
5. **Choose Server Size**: 
   - **Recommended**: 4 vCPU, 8GB RAM, 160GB SSD (~$24/month)
   - **Budget**: 2 vCPU, 4GB RAM, 80GB SSD (~$12/month)
   - **High Performance**: 8 vCPU, 16GB RAM, 320GB SSD (~$48/month)
6. **Operating System**: Ubuntu 22.04 LTS
7. **SSH Keys**: Add your SSH public key (recommended)
8. **Server Label**: "open-swe-production" (or similar)
9. **Click "Deploy Now"**

### 1.2 Initial Server Access
```bash
# SSH into your server (replace with your server IP)
ssh root@YOUR_SERVER_IP

# Update system packages
apt update && apt upgrade -y
```

## 🌐 Step 2: Domain Configuration (Optional but Recommended)

### 2.1 DNS Setup
If you have a domain name, configure DNS:

1. **A Record**: `yourdomain.com` → `YOUR_SERVER_IP`
2. **A Record**: `www.yourdomain.com` → `YOUR_SERVER_IP`

### 2.2 Verify DNS Propagation
```bash
# Check DNS resolution
nslookup yourdomain.com
dig yourdomain.com
```

## 📦 Step 3: Download and Run Deployment Script

### 3.1 Download the Script
```bash
# Download the deployment script
wget https://raw.githubusercontent.com/yourusername/open-swe/main/vultr-full-stack-deploy.sh

# Make it executable
chmod +x vultr-full-stack-deploy.sh
```

### 3.2 Run the Deployment
```bash
# With domain (recommended)
REPO_URL=https://github.com/yourusername/open-swe.git \
SERVER_DOMAIN=yourdomain.com \
./vultr-full-stack-deploy.sh

# Without domain (IP-only access)
REPO_URL=https://github.com/yourusername/open-swe.git \
./vultr-full-stack-deploy.sh
```

### 3.3 What the Script Does
The deployment script automatically:
1. ✅ Updates system packages
2. ✅ Installs Node.js 18, Yarn, PM2, Nginx
3. ✅ Creates application user (`openswe`)
4. ✅ Clones your repository
5. ✅ Builds both web app and backend
6. ✅ Creates environment file templates
7. ✅ Configures PM2 for process management
8. ✅ Sets up Nginx with SSL-ready configuration
9. ✅ Configures UFW firewall
10. ✅ Installs SSL certificate (if domain provided)
11. ✅ Starts applications

## 🔧 Step 4: Configure Environment Variables

### 4.1 Edit Web App Environment
```bash
# Edit web app environment
sudo -u openswe nano /home/openswe/open-swe/apps/web/.env.production
```

**Update these values**:
```env
# Replace with your actual values
NEXTAUTH_SECRET=your-secure-random-string-here
GITHUB_CLIENT_ID=your-github-oauth-client-id
GITHUB_CLIENT_SECRET=your-github-oauth-client-secret
```

### 4.2 Edit Backend Environment
```bash
# Edit backend environment
sudo -u openswe nano /home/openswe/open-swe/apps/agent-mojo/.env.production
```

**Update these critical values**:
```env
# AI API Keys (REQUIRED)
OPENAI_API_KEY=sk-your-openai-api-key-here
ANTHROPIC_API_KEY=sk-ant-your-anthropic-key-here
GITHUB_TOKEN=ghp_your-github-token-here
DAYTONA_API_KEY=your-daytona-api-key-here
FIRECRAWL_API_KEY=fc-your-firecrawl-key-here

# Security
JWT_SECRET=your-jwt-secret-here
```

### 4.3 Restart Applications
```bash
# Restart both applications to load new environment
sudo -u openswe pm2 restart all

# Verify applications are running
sudo -u openswe pm2 status
```

## 🔍 Step 5: Verify Deployment

### 5.1 Check Application Status
```bash
# Check PM2 processes
sudo -u openswe pm2 status

# Check application logs
sudo -u openswe pm2 logs

# Check Nginx status
systemctl status nginx
```

### 5.2 Test Web Access
```bash
# Test health endpoint
curl http://localhost/health

# Test web app (should return HTML)
curl http://localhost/

# Test API endpoint
curl http://localhost/api/health
```

### 5.3 Browser Testing
1. **Open your domain**: `https://yourdomain.com` (or `http://YOUR_SERVER_IP`)
2. **Verify SSL**: Look for the lock icon in browser
3. **Test functionality**: Try creating a new project or agent interaction
4. **Check browser console**: Look for any JavaScript errors

## 🔒 Step 6: Security Hardening (Recommended)

### 6.1 SSH Security
```bash
# Disable root login and password authentication
nano /etc/ssh/sshd_config

# Set these values:
# PermitRootLogin no
# PasswordAuthentication no
# PubkeyAuthentication yes

# Restart SSH
systemctl restart ssh
```

### 6.2 Automatic Updates
```bash
# Install unattended upgrades
apt install unattended-upgrades

# Configure automatic security updates
dpkg-reconfigure -plow unattended-upgrades
```

### 6.3 Fail2Ban (Optional)
```bash
# Install fail2ban for intrusion prevention
apt install fail2ban

# Create basic configuration
cp /etc/fail2ban/jail.conf /etc/fail2ban/jail.local

# Start and enable
systemctl start fail2ban
systemctl enable fail2ban
```

## 📊 Step 7: Monitoring Setup

### 7.1 Basic Monitoring Commands
```bash
# System resources
htop

# Application monitoring
sudo -u openswe pm2 monit

# Disk usage
df -h

# Memory usage
free -h

# Network connections
ss -tulpn
```

### 7.2 Log Monitoring
```bash
# Application logs
tail -f /var/log/openswe/*.log

# Nginx logs
tail -f /var/log/nginx/access.log
tail -f /var/log/nginx/error.log

# System logs
journalctl -f
```

### 7.3 Set Up Log Rotation
```bash
# Create logrotate configuration
sudo tee /etc/logrotate.d/openswe > /dev/null << 'EOF'
/var/log/openswe/*.log {
    daily
    missingok
    rotate 14
    compress
    delaycompress
    notifempty
    create 0644 openswe openswe
    postrotate
        sudo -u openswe pm2 reloadLogs
    endscript
}
EOF
```

## 🔄 Step 8: Backup Strategy

### 8.1 Vultr Snapshots
1. **Go to Vultr Dashboard**
2. **Select your server**
3. **Click "Snapshots" tab**
4. **Enable automatic snapshots** (recommended: daily)

### 8.2 Application Backup Script
```bash
# Create backup script
sudo tee /home/openswe/backup.sh > /dev/null << 'EOF'
#!/bin/bash
BACKUP_DIR="/home/openswe/backups"
DATE=$(date +%Y%m%d_%H%M%S)

mkdir -p $BACKUP_DIR

# Backup application code and configs
tar -czf $BACKUP_DIR/openswe_$DATE.tar.gz \
    /home/openswe/open-swe \
    /etc/nginx/sites-available/openswe \
    /var/log/openswe

# Keep only last 7 backups
find $BACKUP_DIR -name "openswe_*.tar.gz" -mtime +7 -delete

echo "Backup completed: openswe_$DATE.tar.gz"
EOF

# Make executable
chmod +x /home/openswe/backup.sh

# Add to crontab for daily backups
(crontab -u openswe -l 2>/dev/null; echo "0 2 * * * /home/openswe/backup.sh") | crontab -u openswe -
```

## 🚨 Troubleshooting

### Common Issues

#### 1. Applications Not Starting
```bash
# Check PM2 status
sudo -u openswe pm2 status

# Check logs for errors
sudo -u openswe pm2 logs

# Restart applications
sudo -u openswe pm2 restart all
```

#### 2. Nginx Configuration Errors
```bash
# Test Nginx configuration
nginx -t

# Check Nginx logs
tail -f /var/log/nginx/error.log

# Restart Nginx
systemctl restart nginx
```

#### 3. SSL Certificate Issues
```bash
# Check certificate status
certbot certificates

# Renew certificate manually
certbot renew --dry-run

# Force renewal
certbot renew --force-renewal
```

#### 4. Out of Memory Errors
```bash
# Check memory usage
free -h

# Add swap space (if not already present)
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

#### 5. High CPU Usage
```bash
# Check top processes
htop

# Check PM2 processes
sudo -u openswe pm2 monit

# Consider upgrading server or optimizing code
```

### Getting Help

1. **Check logs first**: Most issues are logged
2. **Verify environment variables**: Ensure all API keys are set
3. **Test individual components**: Web app, API, Nginx separately
4. **Check firewall**: Ensure ports 80 and 443 are open
5. **Verify DNS**: Ensure domain points to correct IP

## 📈 Scaling and Optimization

### When to Scale Up
- CPU usage consistently > 70%
- Memory usage > 80%
- Response times > 2 seconds
- Frequent application restarts

### Scaling Options
1. **Vertical Scaling**: Upgrade to larger Vultr instance
2. **Horizontal Scaling**: Add load balancer + multiple servers
3. **Database Separation**: Move to dedicated database server
4. **CDN Integration**: Use Cloudflare or similar for static assets

## ✅ Post-Deployment Checklist

- [ ] Applications running and accessible
- [ ] SSL certificate installed and working
- [ ] All environment variables configured
- [ ] Monitoring and logging set up
- [ ] Backup strategy implemented
- [ ] Security hardening completed
- [ ] DNS properly configured
- [ ] Performance testing completed
- [ ] Documentation updated with server details

## 🎉 Success!

Your Open-SWE application should now be running at:
- **With Domain**: `https://yourdomain.com`
- **Without Domain**: `http://YOUR_SERVER_IP`

The deployment includes:
- ✅ Next.js web application on port 3000 (proxied)
- ✅ Agent-Mojo backend API on port 8000 (proxied)
- ✅ Nginx reverse proxy with SSL
- ✅ PM2 process management
- ✅ Automatic startup on reboot
- ✅ Log rotation and monitoring
- ✅ Firewall configuration

---

**Need help?** Check the troubleshooting section above or review the logs for specific error messages.