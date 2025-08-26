# Inter-Service Communication Setup

This document outlines how the Vercel frontend communicates with the Vultr backend in the Open SWE deployment architecture.

## Architecture Overview

```
┌─────────────────┐    HTTPS/API Calls    ┌─────────────────┐
│                 │ ────────────────────► │                 │
│  Vercel Frontend│                       │  Vultr Backend  │
│  (Next.js App)  │ ◄──────────────────── │  (LangGraph)    │
│                 │    JSON Responses     │                 │
└─────────────────┘                       └─────────────────┘
```

## Communication Flow

### 1. Frontend API Proxy Route

The Next.js frontend uses a proxy route at `/api/*` to forward requests to the backend:

**File**: `apps/web/src/app/api/[...path]/route.ts`

- **Purpose**: Acts as a proxy between the frontend and LangGraph backend
- **Functionality**: 
  - Forwards all API requests to the backend
  - Injects authentication headers and secrets
  - Handles CORS and security headers

### 2. Environment Configuration

#### Frontend Environment Variables
```bash
# Public API URL (accessible to browser)
NEXT_PUBLIC_API_URL="https://your-vercel-app.vercel.app/api"

# Backend API URL (server-side only)
LANGGRAPH_API_URL="https://your-vultr-server.com:2024"
```

#### Backend Environment Variables
```bash
# Application URL for callbacks
OPEN_SWE_APP_URL="https://your-vercel-app.vercel.app"

# Port for LangGraph server
PORT="2024"
```

### 3. Request Flow

1. **Browser Request**: User action triggers API call to `https://your-vercel-app.vercel.app/api/endpoint`
2. **Proxy Processing**: Next.js proxy route receives the request
3. **Backend Forward**: Proxy forwards request to `https://your-vultr-server.com:2024/endpoint`
4. **Backend Processing**: LangGraph server processes the request
5. **Response Chain**: Response flows back through proxy to browser

### 4. Authentication & Security

#### GitHub App Integration
- **Frontend**: Handles OAuth flow and stores session tokens
- **Backend**: Validates GitHub App credentials for repository access
- **Shared**: Both services use the same `GITHUB_APP_*` environment variables

#### Secrets Encryption
- **Shared Key**: Both services use the same `SECRETS_ENCRYPTION_KEY`
- **Purpose**: Allows frontend to encrypt secrets that backend can decrypt
- **Usage**: API keys, tokens, and sensitive configuration

### 5. CORS Configuration

#### Frontend (Vercel)
```javascript
// Next.js automatically handles CORS for API routes
// Custom headers added in proxy route for backend communication
```

#### Backend (Vultr)
```nginx
# Nginx configuration
add_header Access-Control-Allow-Origin "https://your-vercel-app.vercel.app";
add_header Access-Control-Allow-Methods "GET, POST, PUT, DELETE, OPTIONS";
add_header Access-Control-Allow-Headers "Content-Type, Authorization";
```

### 6. SSL/TLS Configuration

#### Frontend (Vercel)
- **Automatic**: Vercel provides automatic SSL certificates
- **Domain**: Custom domain or `*.vercel.app` subdomain

#### Backend (Vultr)
- **Let's Encrypt**: Automated SSL certificate via Certbot
- **Nginx**: Terminates SSL and proxies to Node.js application
- **Port**: HTTPS on 443, HTTP redirects to HTTPS

### 7. Health Checks & Monitoring

#### Frontend Health Check
```javascript
// GET /api/health
// Returns: { status: "ok", timestamp: "..." }
```

#### Backend Health Check
```javascript
// GET /health
// Returns: { status: "ok", graphs: [...], timestamp: "..." }
```

### 8. Error Handling

#### Network Errors
- **Frontend**: Displays user-friendly error messages
- **Retry Logic**: Automatic retry for transient failures
- **Fallback**: Graceful degradation when backend is unavailable

#### Authentication Errors
- **401 Unauthorized**: Redirects to GitHub OAuth flow
- **403 Forbidden**: Shows permission error with instructions

### 9. Development vs Production

#### Development
```bash
# Frontend
NEXT_PUBLIC_API_URL="http://localhost:3000/api"
LANGGRAPH_API_URL="http://localhost:2024"

# Backend
OPEN_SWE_APP_URL="http://localhost:3000"
```

#### Production
```bash
# Frontend
NEXT_PUBLIC_API_URL="https://your-vercel-app.vercel.app/api"
LANGGRAPH_API_URL="https://your-vultr-server.com:2024"

# Backend
OPEN_SWE_APP_URL="https://your-vercel-app.vercel.app"
```

### 10. Troubleshooting Communication Issues

#### Common Issues
1. **CORS Errors**: Check domain whitelist in Nginx configuration
2. **SSL Certificate Issues**: Verify Let's Encrypt certificates are valid
3. **Timeout Errors**: Check network connectivity and firewall rules
4. **Authentication Failures**: Verify shared environment variables match

#### Debugging Steps
1. **Check Frontend Logs**: Vercel function logs for proxy route errors
2. **Check Backend Logs**: PM2 logs on Vultr server
3. **Network Testing**: Use curl to test direct backend connectivity
4. **SSL Verification**: Check certificate validity and chain

#### Monitoring Commands
```bash
# Check backend status
curl -k https://your-vultr-server.com:2024/health

# Check frontend proxy
curl https://your-vercel-app.vercel.app/api/health

# Monitor backend logs
pm2 logs agent-mojo

# Check Nginx status
sudo systemctl status nginx
```

## Security Considerations

1. **Environment Variables**: Never expose backend URLs or secrets to the browser
2. **HTTPS Only**: All communication must use HTTPS in production
3. **CORS Restrictions**: Limit allowed origins to your frontend domain
4. **Rate Limiting**: Implement rate limiting on both frontend and backend
5. **Input Validation**: Validate all data at both service boundaries

## Performance Optimization

1. **Connection Pooling**: Use HTTP keep-alive for backend connections
2. **Caching**: Implement appropriate caching headers
3. **Compression**: Enable gzip compression in Nginx
4. **CDN**: Leverage Vercel's edge network for static assets
5. **Database Connections**: Use connection pooling in the backend

This communication setup ensures secure, reliable, and performant interaction between your Vercel frontend and Vultr backend services.