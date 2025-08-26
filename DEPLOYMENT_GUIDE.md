# Deployment Guide: Vercel Frontend + Vultr Backend

This guide provides step-by-step instructions for deploying the Open SWE project with the frontend on Vercel and the backend on Vultr.

## Architecture Overview

- **Frontend (Web App)**: Next.js application deployed on Vercel
- **Backend (Agent)**: LangGraph agent application deployed on Vultr VPS
- **Communication**: HTTPS API calls between frontend and backend

## Prerequisites

- Node.js 18+ installed locally
- Yarn package manager
- Git repository access
- Vercel account
- Vultr account
- Domain name (optional but recommended)

## Part 1: Frontend Deployment on Vercel

### Step 1: Prepare the Frontend

1. **Navigate to the web app directory:**
   ```bash
   cd apps/web
   ```

2. **Install dependencies and test build locally:**
   ```bash
   yarn install
   yarn build
   ```

3. **Verify the `vercel.json` configuration** (already created):
   - Located at `apps/web/vercel.json`
   - Configured for Next.js with proper routing

### Step 2: Deploy to Vercel

#### Option A: Using Vercel CLI (Recommended)

1. **Install Vercel CLI:**
   ```bash
   npm i -g vercel
   ```

2. **Login to Vercel:**
   ```bash
   vercel login
   ```

3. **Deploy from the web app directory:**
   ```bash
   cd apps/web
   vercel
   ```

4. **Follow the prompts:**
   - Set up and deploy: `Y`
   - Which scope: Select your account/team
   - Link to existing project: `N` (for first deployment)
   - Project name: `open-swe-web` (or your preferred name)
   - Directory: `./` (current directory)

#### Option B: Using Vercel Dashboard

1. **Connect GitHub repository:**
   - Go to [vercel.com](https://vercel.com)
   - Click "New Project"
   - Import your GitHub repository

2. **Configure build settings:**
   - Framework Preset: `Next.js`
   - Build Command: `yarn build`
   - Install Command: `yarn install`
   - Output Directory: `.next`

### Step 3: Configure Environment Variables on Vercel

In your Vercel project dashboard, add these environment variables:

```bash
# Required for production
NODE_ENV=production
NEXTAUTH_URL=https://your-vercel-app.vercel.app
NEXTAUTH_SECRET=your-nextauth-secret-key

# API Configuration (will be updated after backend deployment)
NEXT_PUBLIC_API_URL=https://your-vultr-backend.com/api
LANGGRAPH_API_URL=https://your-vultr-backend.com

# GitHub App Configuration
GITHUB_APP_NAME=Ageent Mojo
GITHUB_APP_ID=1806822
NEXT_PUBLIC_GITHUB_APP_CLIENT_ID=Iv23li0lUFhtsgOZlji9
GITHUB_APP_CLIENT_SECRET=your-github-app-client-secret
GITHUB_APP_PRIVATE_KEY=your-github-app-private-key
GITHUB_WEBHOOK_SECRET=your-github-webhook-secret
GITHUB_APP_REDIRECT_URI=https://your-vercel-app.vercel.app/api/auth/github/callback

# Other Configuration
OPEN_SWE_APP_URL=https://your-vercel-app.vercel.app
SECRETS_ENCRYPTION_KEY=your-32-byte-hex-encryption-key
```

## Part 2: Backend Deployment on Vultr

### Step 1: Create Vultr VPS

1. **Login to Vultr Dashboard:**
   - Go to [vultr.com](https://vultr.com)
   - Create account or login

2. **Deploy new server:**
   - Click "Deploy New Server"
   - Choose server type: `Cloud Compute`
   - Location: Choose closest to your users
   - Server Image: `Ubuntu 22.04 LTS`
   - Server Size: At least `2 vCPU, 4GB RAM` (recommended: `4 vCPU, 8GB RAM`)
   - Additional Features: Enable `Auto Backups` (optional)
   - SSH Keys: Add your SSH key or use password

3. **Note the server IP address** once deployed

### Step 2: Server Setup

1. **Connect to your server:**
   ```bash
   ssh root@your-server-ip
   ```

2. **Update system:**
   ```bash
   apt update && apt upgrade -y
   ```

3. **Install Node.js 18:**
   ```bash
   curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -
   apt-get install -y nodejs
   ```

4. **Install Yarn:**
   ```bash
   npm install -g yarn
   ```

5. **Install PM2 (Process Manager):**
   ```bash
   npm install -g pm2
   ```

6. **Install Nginx:**
   ```bash
   apt install -y nginx
   ```

7. **Create application user:**
   ```bash
   adduser --system --group --home /opt/open-swe openswe
   ```

### Step 3: Deploy Backend Application

1. **Clone repository:**
   ```bash
   cd /opt/open-swe
   git clone https://github.com/your-username/open-swe.git .
   chown -R openswe:openswe /opt/open-swe
   ```

2. **Switch to application user:**
   ```bash
   su - openswe
   cd /opt/open-swe
   ```

3. **Install dependencies:**
   ```bash
   yarn install
   ```

4. **Build the agent application:**
   ```bash
   cd apps/agent-mojo
   yarn build
   ```

### Step 4: Configure Environment Variables

1. **Create production environment file:**
   ```bash
   cp apps/agent-mojo/.env.example apps/agent-mojo/.env.production
   ```

2. **Edit the environment file:**
   ```bash
   nano apps/agent-mojo/.env.production
   ```

3. **Add your configuration:**
   ```bash
   # Server Configuration
   NODE_ENV=production
   PORT=3001
   
   # LLM Provider Keys
   OPENAI_API_KEY=your-openai-api-key
   ANTHROPIC_API_KEY=your-anthropic-api-key
   GOOGLE_API_KEY=your-google-api-key
   
   # Infrastructure
   DAYTONA_API_KEY=your-daytona-api-key
   FIRECRAWL_API_KEY=your-firecrawl-api-key
   
   # GitHub App Configuration
   GITHUB_APP_NAME=Ageent Mojo
   GITHUB_APP_ID=1806822
   GITHUB_APP_PRIVATE_KEY=your-github-app-private-key
   GITHUB_WEBHOOK_SECRET=your-github-webhook-secret
   GITHUB_APP_CLIENT_SECRET=your-github-app-client-secret
   GITHUB_APP_REDIRECT_URI=https://your-vercel-app.vercel.app/api/auth/github/callback
   
   # Other Configuration
   OPEN_SWE_APP_URL=https://your-vercel-app.vercel.app
   SECRETS_ENCRYPTION_KEY=your-32-byte-hex-encryption-key
   SKIP_CI_UNTIL_LAST_COMMIT=true
   OPEN_SWE_LOCAL_MODE=false
   ```

### Step 5: Configure PM2

1. **Create PM2 ecosystem file:**
   ```bash
   nano ecosystem.config.js
   ```

2. **Add PM2 configuration:**
   ```javascript
   module.exports = {
     apps: [{
       name: 'open-swe-agent',
       cwd: '/opt/open-swe/apps/agent-mojo',
       script: 'yarn',
       args: 'start',
       env: {
         NODE_ENV: 'production',
         PORT: 3001
       },
       env_file: '.env.production',
       instances: 1,
       exec_mode: 'fork',
       watch: false,
       max_memory_restart: '1G',
       error_file: '/var/log/open-swe/error.log',
       out_file: '/var/log/open-swe/out.log',
       log_file: '/var/log/open-swe/combined.log',
       time: true
     }]
   };
   ```

3. **Create log directory:**
   ```bash
   sudo mkdir -p /var/log/open-swe
   sudo chown openswe:openswe /var/log/open-swe
   ```

4. **Start the application:**
   ```bash
   pm2 start ecosystem.config.js
   pm2 save
   pm2 startup
   ```

### Step 6: Configure Nginx

1. **Create Nginx configuration:**
   ```bash
   sudo nano /etc/nginx/sites-available/open-swe
   ```

2. **Add Nginx configuration:**
   ```nginx
   server {
       listen 80;
       server_name your-domain.com;  # Replace with your domain or server IP
       
       # Security headers
       add_header X-Frame-Options "SAMEORIGIN" always;
       add_header X-XSS-Protection "1; mode=block" always;
       add_header X-Content-Type-Options "nosniff" always;
       add_header Referrer-Policy "no-referrer-when-downgrade" always;
       add_header Content-Security-Policy "default-src 'self' http: https: data: blob: 'unsafe-inline'" always;
       
       location / {
           proxy_pass http://localhost:3001;
           proxy_http_version 1.1;
           proxy_set_header Upgrade $http_upgrade;
           proxy_set_header Connection 'upgrade';
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto $scheme;
           proxy_cache_bypass $http_upgrade;
           proxy_read_timeout 300s;
           proxy_connect_timeout 75s;
       }
   }
   ```

3. **Enable the site:**
   ```bash
   sudo ln -s /etc/nginx/sites-available/open-swe /etc/nginx/sites-enabled/
   sudo nginx -t
   sudo systemctl restart nginx
   ```

### Step 7: SSL Certificate (Optional but Recommended)

1. **Install Certbot:**
   ```bash
   sudo apt install certbot python3-certbot-nginx -y
   ```

2. **Obtain SSL certificate:**
   ```bash
   sudo certbot --nginx -d your-domain.com
   ```

## Part 3: Connect Frontend and Backend

### Step 1: Update Vercel Environment Variables

Update these variables in your Vercel project:

```bash
NEXT_PUBLIC_API_URL=https://your-domain.com/api
LANGGRAPH_API_URL=https://your-domain.com
```

### Step 2: Update Backend CORS Configuration

Ensure your backend allows requests from your Vercel domain. This should be configured in your agent application.

### Step 3: Test the Connection

1. **Test backend directly:**
   ```bash
   curl https://your-domain.com/health
   ```

2. **Test frontend:**
   - Visit your Vercel app
   - Check browser console for any CORS or connection errors
   - Test GitHub authentication flow

## Part 4: Monitoring and Maintenance

### Backend Monitoring

1. **Check PM2 status:**
   ```bash
   pm2 status
   pm2 logs
   ```

2. **Monitor system resources:**
   ```bash
   htop
   df -h
   ```

### Deployment Updates

1. **Frontend updates:**
   - Push to your repository
   - Vercel will auto-deploy (if connected to Git)
   - Or use `vercel --prod` for manual deployment

2. **Backend updates:**
   ```bash
   cd /opt/open-swe
   git pull
   yarn install
   cd apps/agent-mojo
   yarn build
   pm2 restart open-swe-agent
   ```

### Backup Strategy

1. **Database backups** (if using a database)
2. **Environment file backups**
3. **Regular Vultr snapshots**
4. **Code repository backups**

## Troubleshooting

### Common Issues

1. **Frontend can't connect to backend:**
   - Check CORS configuration
   - Verify environment variables
   - Check SSL certificate

2. **Backend not starting:**
   - Check PM2 logs: `pm2 logs`
   - Verify environment variables
   - Check port availability: `netstat -tlnp | grep 3001`

3. **GitHub authentication issues:**
   - Verify GitHub App configuration
   - Check redirect URIs
   - Validate webhook URLs

4. **Monorepo build issues:**
   - **Build failures**: Check environment variables are set correctly
   - **Dependency resolution issues**: 
     - Error: "@open-swe/shared: Not found" - This indicates Vercel cannot find the shared package
     - Error: "No Next.js version detected" - This can occur when dependencies aren't properly resolved
     - Solution: Configure `vercel.json` to navigate to monorepo root for dependency installation:
       ```json
       {
         "installCommand": "cd ../.. && yarn install",
         "buildCommand": "cd ../.. && yarn turbo build --filter=@open-swe/web",
         "outputDirectory": ".next",
         "ignoreCommand": "git diff --quiet HEAD^ HEAD ../../"
       }
       ```
     - Note: Do NOT use `rootDirectory` property as it's not supported by Vercel
     - The `cd ../..` commands navigate to the monorepo root where all dependencies can be resolved
   - **API connection issues**: Verify `NEXT_PUBLIC_API_URL` points to your Vultr backend
   - **Authentication issues**: Ensure GitHub App credentials are correct

### Logs and Debugging

1. **Frontend logs:** Check Vercel dashboard
2. **Backend logs:** `pm2 logs open-swe-agent`
3. **Nginx logs:** `sudo tail -f /var/log/nginx/error.log`
4. **System logs:** `sudo journalctl -u nginx -f`

## Security Considerations

1. **Keep system updated:** `apt update && apt upgrade`
2. **Use strong passwords and SSH keys**
3. **Configure firewall:** `ufw enable`
4. **Regular security audits**
5. **Monitor access logs**
6. **Use environment variables for secrets**
7. **Enable fail2ban for SSH protection**

## Cost Optimization

1. **Vercel:** Free tier available, pay for usage
2. **Vultr:** Start with smaller instance, scale as needed
3. **Monitor resource usage**
4. **Use CDN for static assets**
5. **Implement caching strategies**

---

**Note:** Replace all placeholder values (your-domain.com, API keys, etc.) with your actual values. Keep your environment variables and secrets secure and never commit them to version control.