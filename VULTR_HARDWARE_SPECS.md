# Vultr Hardware Specifications for Open-SWE Full-Stack Deployment

This document outlines the recommended Vultr server specifications for deploying the complete Open-SWE monorepo (Next.js web app + Agent-Mojo backend) on a single server.

## 🚀 Recommended Configurations

### Development/Testing Environment
**Vultr Plan: Regular Performance (2 vCPU, 4GB RAM)**
- **vCPUs**: 2
- **RAM**: 4GB
- **Storage**: 80GB SSD
- **Bandwidth**: 3TB
- **Monthly Cost**: ~$12/month

**Use Case**: 
- Development testing
- Small team demos
- Low-traffic staging environments
- Up to 10 concurrent users

### Production Environment (Recommended)
**Vultr Plan: Regular Performance (4 vCPU, 8GB RAM)**
- **vCPUs**: 4
- **RAM**: 8GB
- **Storage**: 160GB SSD
- **Bandwidth**: 4TB
- **Monthly Cost**: ~$24/month

**Use Case**:
- Production deployments
- Medium-traffic applications
- 50-100 concurrent users
- AI model processing with reasonable response times
- Sufficient for most business use cases

### High-Performance Production
**Vultr Plan: Regular Performance (8 vCPU, 16GB RAM)**
- **vCPUs**: 8
- **RAM**: 16GB
- **Storage**: 320GB SSD
- **Bandwidth**: 5TB
- **Monthly Cost**: ~$48/month

**Use Case**:
- High-traffic production environments
- 200+ concurrent users
- Heavy AI model processing
- Multiple simultaneous code analysis tasks
- Enterprise-level deployments

### Enterprise/Scale Environment
**Vultr Plan: Regular Performance (16 vCPU, 32GB RAM)**
- **vCPUs**: 16
- **RAM**: 32GB
- **Storage**: 640GB SSD
- **Bandwidth**: 6TB
- **Monthly Cost**: ~$96/month

**Use Case**:
- Large-scale enterprise deployments
- 500+ concurrent users
- Multiple AI models running simultaneously
- High-frequency code analysis and generation
- Mission-critical applications

## 📊 Resource Allocation Breakdown

### Memory Usage Estimates
- **Node.js Runtime**: ~200MB base per process
- **Next.js Web App**: 500MB - 1GB (depending on traffic)
- **Agent-Mojo Backend**: 1GB - 4GB (depending on AI model usage)
- **System Overhead**: 500MB - 1GB
- **Nginx**: 50MB - 200MB
- **PM2**: 50MB - 100MB
- **Buffer for AI Processing**: 1GB - 8GB (varies by model complexity)

### CPU Usage Patterns
- **Next.js SSR**: Moderate CPU usage during page rendering
- **Agent-Mojo AI Processing**: High CPU bursts during code analysis
- **Background Tasks**: Low to moderate continuous usage
- **Nginx**: Minimal CPU usage for reverse proxy

### Storage Requirements
- **Application Code**: ~500MB
- **Node Modules**: ~1GB
- **Logs**: 100MB - 1GB (with log rotation)
- **Temporary Files**: 500MB - 2GB
- **System**: 10GB - 15GB
- **Growth Buffer**: 20GB - 50GB

## 🌍 Recommended Regions

### Primary Regions (Best Performance)
- **North America**: 
  - New York (NJ) - Low latency for US East Coast
  - Los Angeles (CA) - Low latency for US West Coast
  - Toronto (Canada) - Good for Canadian users

- **Europe**:
  - London (UK) - Excellent for UK/Western Europe
  - Frankfurt (Germany) - Central Europe hub
  - Amsterdam (Netherlands) - Good European connectivity

- **Asia-Pacific**:
  - Tokyo (Japan) - Best for East Asia
  - Singapore - Southeast Asia hub
  - Sydney (Australia) - Oceania coverage

### Selection Criteria
1. **User Base Location**: Choose closest to your primary users
2. **Compliance Requirements**: Consider data residency laws
3. **Network Performance**: Test latency from your location
4. **Backup Strategy**: Consider multi-region for disaster recovery

## ⚡ Performance Optimization Settings

### Operating System
- **Recommended**: Ubuntu 22.04 LTS
- **Alternative**: Ubuntu 20.04 LTS
- **Reason**: Best Node.js ecosystem support and long-term stability

### Additional Optimizations

#### For 4GB RAM Systems
```bash
# Add swap space for memory bursts
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

#### For High-Traffic Deployments
```bash
# Increase file descriptor limits
echo '* soft nofile 65536' | sudo tee -a /etc/security/limits.conf
echo '* hard nofile 65536' | sudo tee -a /etc/security/limits.conf

# Optimize network settings
echo 'net.core.somaxconn = 65536' | sudo tee -a /etc/sysctl.conf
echo 'net.ipv4.tcp_max_syn_backlog = 65536' | sudo tee -a /etc/sysctl.conf
sudo sysctl -p
```

## 📈 Scaling Considerations

### Vertical Scaling (Recommended First Step)
1. **Monitor Resource Usage**:
   ```bash
   # CPU and Memory monitoring
   htop
   
   # Application-specific monitoring
   sudo -u openswe pm2 monit
   
   # Disk usage
   df -h
   ```

2. **Upgrade Triggers**:
   - CPU usage consistently > 70%
   - Memory usage > 80%
   - Response times > 2 seconds
   - Frequent out-of-memory errors

### Horizontal Scaling (Advanced)
For very high-traffic scenarios, consider:
1. **Load Balancer + Multiple App Servers**
2. **Separate Database Server**
3. **Redis Cluster for Session Management**
4. **CDN for Static Assets**

## 🔧 Monitoring and Maintenance

### Essential Monitoring
- **System Resources**: CPU, Memory, Disk, Network
- **Application Performance**: Response times, error rates
- **Log Analysis**: Error patterns, usage trends
- **SSL Certificate Expiry**: Automated renewal verification

### Maintenance Schedule
- **Daily**: Log review, basic health checks
- **Weekly**: Security updates, performance review
- **Monthly**: Full system update, backup verification
- **Quarterly**: Capacity planning review

## 💰 Cost Optimization Tips

1. **Start Small**: Begin with 4GB RAM configuration
2. **Monitor Usage**: Use built-in Vultr monitoring
3. **Reserved Instances**: Consider annual billing for discounts
4. **Resource Cleanup**: Regular log rotation and temp file cleanup
5. **Efficient Caching**: Implement proper caching strategies

## 🚨 Backup and Disaster Recovery

### Automated Backups
- **Vultr Snapshots**: Daily automated snapshots
- **Application Data**: Database backups if using external DB
- **Configuration Files**: Version control for deployment scripts

### Recovery Strategy
1. **Infrastructure**: Snapshot restoration (15-30 minutes)
2. **Application**: Git-based redeployment (10-15 minutes)
3. **Data**: Database restoration from backups
4. **DNS**: Update records if IP changes

## 📋 Deployment Checklist

### Pre-Deployment
- [ ] Choose appropriate server size based on expected load
- [ ] Select optimal region for your user base
- [ ] Prepare domain name and DNS configuration
- [ ] Gather all required API keys and secrets

### Post-Deployment
- [ ] Configure monitoring and alerting
- [ ] Set up automated backups
- [ ] Implement log rotation
- [ ] Test disaster recovery procedures
- [ ] Document access credentials and procedures

## 🔗 Quick Start Commands

### Deploy to Recommended Production Server (4 vCPU, 8GB RAM)
```bash
# On your Vultr server
wget https://raw.githubusercontent.com/yourusername/open-swe/main/vultr-full-stack-deploy.sh
chmod +x vultr-full-stack-deploy.sh

# Run deployment
REPO_URL=https://github.com/yourusername/open-swe.git \
SERVER_DOMAIN=yourdomain.com \
sudo ./vultr-full-stack-deploy.sh
```

### Monitor Resource Usage
```bash
# Real-time monitoring
htop

# Application monitoring
sudo -u openswe pm2 monit

# Nginx status
sudo systemctl status nginx

# Check logs
tail -f /var/log/openswe/*.log
```

---

**Note**: These specifications are recommendations based on typical usage patterns. Monitor your actual usage and adjust accordingly. Start with the recommended production configuration (4 vCPU, 8GB RAM) and scale up as needed.