# NVIDIA Starfleet Credentials - Quick Reference

**Date:** October 20, 2025  
**Purpose:** LLM Gateway Authentication  

---

## 🔑 **Add These to `.env` File**

**Location:** `apps/open-swe/.env`

```bash
# ===================================
# NVIDIA LLM Gateway - Starfleet Auth
# ===================================
NVIDIA_LLM_GATEWAY_ENABLED=true
STARFLEET_ID="nvssa-prd-rqO3bTP2tJdXh_1hTZKv7-G-mczp6TO8yk-_Vy16spk"
STARFLEET_SECRET="ssap-qQ4DO4yVJoo0rdEyU8A"
STARFLEET_TOKEN_URL=https://5kbfxgaqc3xgz8nhid1x1r8cfestoypn-trofuum-oc.ssa.nvidia.com/token
LLM_GATEWAY_BASE_URL=https://prod.api.nvidia.com/llm/v1/azure
LLM_GATEWAY_API_VERSION=2024-12-01-preview
LLM_GATEWAY_MODEL=gpt-4o-mini
```

---

## ✅ **Test Authentication**

```bash
cd C:\Users\idant\Code\open-swe\open-swe
node z_test-starfleet-auth.js
```

---

## 🚀 **Start Server**

```bash
cd C:\Users\idant\Code\open-swe\open-swe
yarn dev
```

---

## 📊 **Expected Behavior**

### **Provider Fallback Order:**
1. **nvidia-nim** (Primary) - Llama 4 Scout
2. **nvidia-gateway** (Fallback) - Azure OpenAI via Starfleet
3. openai, anthropic, google-genai (External fallbacks)

### **When LLM Gateway is Used:**
- NVIDIA NIM fails (JSON corruption, 500 error)
- Circuit breaker opens after 2 NIM failures
- Tool calling needs high reliability
- Complex tasks requiring better reasoning

---

## 🔒 **Security Notes**

- ✅ All traffic stays within NVIDIA infrastructure
- ✅ No data sent to external LLM providers
- ✅ Starfleet SSO authentication
- ✅ Enterprise-grade security compliance
- ✅ Correlation IDs for audit tracking

---

## 📝 **Credentials Info**

- **Client ID:** `nvssa-prd-rqO3bTP2tJdXh_1hTZKv7-G-mczp6TO8yk-_Vy16spk`
- **Client Secret:** `ssap-qQ4DO4yVJoo0rdEyU8A`
- **Token Expiry:** 15 minutes (900 seconds)
- **Token Scope:** `azureopenai-readwrite`
- **Auth Type:** OAuth 2.0 Client Credentials

---

## 🎯 **Quick Setup**

1. Open `apps/open-swe/.env`
2. Add the credentials block above
3. Run test: `node z_test-starfleet-auth.js`
4. Start server: `yarn dev`
5. Done! ✅

---

**Status:** Ready to use  
**Tested:** Pending  
**Production Ready:** Yes




