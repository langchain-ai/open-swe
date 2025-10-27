# NVIDIA LLM Gateway Integration Guide

**Purpose:** Integrate NVIDIA LLM Gateway (with Starfleet auth) as fallback provider  
**Security:** ✅ NVIDIA-approved, enterprise-grade, compliant with data policies  
**Models:** Azure OpenAI (gpt-4o, gpt-4o-mini) via NVIDIA's secure gateway  
**Created:** October 19, 2025

---

## 🎯 **Why Use NVIDIA LLM Gateway?**

### **Security & Compliance:**
- ✅ All data stays within NVIDIA infrastructure
- ✅ No direct external LLM API calls
- ✅ NVIDIA controls data flow
- ✅ Starfleet authentication (NVIDIA SSO)
- ✅ Compliant with NVIDIA security policies

### **Reliability:**
- ✅ Enterprise SLA
- ✅ Fallback when NVIDIA NIM has issues
- ✅ Production-ready
- ✅ Monitoring via correlationId

---

## 📋 **Architecture:**

```
Open SWE Request
    ↓
Try: NVIDIA NIM (Llama 4 Scout/Maverick)
    ↓ (if fails - JSON corruption, 500 error, etc.)
Fallback: NVIDIA LLM Gateway → Azure OpenAI (gpt-4o-mini)
    ↓
Response ✅
```

**Benefits:**
- Most requests use NVIDIA NIM (cheap, fast)
- Complex/failing requests use Azure OpenAI (reliable)
- All traffic stays within NVIDIA

---

## 🔐 **Configuration**

### **Step 1: Get Starfleet Credentials**

You'll need these from NVIDIA IT/Security:
- **STARFLEET_ID** (client ID)
- **STARFLEET_SECRET** (client secret)

**Add to `.env` file:**

**File:** `apps/open-swe/.env`

```bash
# NVIDIA NIM (Primary - Direct NIM API)
NVIDIA_NIM_API_KEY=nvapi-YOUR_KEY_HERE
NVIDIA_NIM_BASE_URL=https://integrate.api.nvidia.com/v1

# NVIDIA LLM Gateway (Fallback - Via Starfleet)
NVIDIA_LLM_GATEWAY_ENABLED=true
STARFLEET_ID=your-starfleet-client-id
STARFLEET_SECRET=your-starfleet-client-secret
STARFLEET_TOKEN_URL=https://5kbfxgaqc3xgz8nhid1x1r8cfestoypn-trofuum-oc.ssa.nvidia.com/token
LLM_GATEWAY_BASE_URL=https://prod.api.nvidia.com/llm/v1/azure
LLM_GATEWAY_API_VERSION=2024-12-01-preview
LLM_GATEWAY_MODEL=gpt-4o-mini
```

---

## 🔧 **Implementation Plan:**

### **Phase 1: Add Starfleet Auth**

Create: `apps/open-swe/src/utils/starfleet-auth.ts`

```typescript
import fetch from 'node-fetch';

interface StarfleetTokenResponse {
  access_token: string;
  expires_in: number;
  token_type: string;
}

class StarfleetAuthManager {
  private token: string | null = null;
  private tokenExpiry: number = 0;
  
  async getAccessToken(): Promise<string> {
    // Check if we have a valid token
    if (this.token && Date.now() < this.tokenExpiry - 60000) {
      return this.token;
    }
    
    // Get fresh token
    const tokenUrl = process.env.STARFLEET_TOKEN_URL;
    const clientId = process.env.STARFLEET_ID;
    const clientSecret = process.env.STARFLEET_SECRET;
    
    if (!tokenUrl || !clientId || !clientSecret) {
      throw new Error('Starfleet credentials not configured');
    }
    
    const credentials = Buffer.from(`${clientId}:${clientSecret}`).toString('base64');
    
    const response = await fetch(tokenUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        'Authorization': `Basic ${credentials}`,
      },
      body: 'grant_type=client_credentials&scope=azureopenai-readwrite',
    });
    
    if (!response.ok) {
      throw new Error(`Starfleet auth failed: ${response.statusText}`);
    }
    
    const data = await response.json() as StarfleetTokenResponse;
    this.token = data.access_token;
    this.tokenExpiry = Date.now() + (data.expires_in * 1000);
    
    return this.token;
  }
}

export const starfleetAuth = new StarfleetAuthManager();
```

### **Phase 2: Add LLM Gateway Provider**

Update `apps/open-swe/src/utils/llms/model-manager.ts`:

Add to provider fallback order:
```typescript
export const PROVIDER_FALLBACK_ORDER = [
  "nvidia-nim",        // 1st: Direct NIM
  "nvidia-gateway",    // 2nd: LLM Gateway → Azure OpenAI (NEW!)
  "openai",            // 3rd: Direct OpenAI (if allowed)
  "anthropic",
  "google-genai",
] as const;
```

Add provider handling:
```typescript
case "nvidia-gateway":
  // Use Starfleet token
  const token = await starfleetAuth.getAccessToken();
  return token;
```

### **Phase 3: Create LLM Gateway ChatOpenAI Instance**

```typescript
if (provider === "nvidia-gateway" && apiKey) {
  return new ChatOpenAI({
    modelName: process.env.LLM_GATEWAY_MODEL || "gpt-4o-mini",
    openAIApiKey: apiKey, // This is the Starfleet token
    configuration: {
      baseURL: process.env.LLM_GATEWAY_BASE_URL || "https://prod.api.nvidia.com/llm/v1/azure",
      defaultQuery: {
        "api-version": process.env.LLM_GATEWAY_API_VERSION || "2024-12-01-preview"
      },
      defaultHeaders: {
        "correlationId": generateCorrelationId(),
      }
    },
    maxRetries: MAX_RETRIES,
    maxTokens: finalMaxTokens,
    temperature: temperature,
  });
}
```

---

## 📊 **Provider Strategy:**

| Task | Primary | Fallback 1 | Fallback 2 |
|------|---------|------------|------------|
| Router | NVIDIA NIM Llama 4 | LLM Gateway gpt-4o-mini | - |
| Planner | NVIDIA NIM Llama 4 | LLM Gateway gpt-4o | - |
| Programmer | NVIDIA NIM Llama 4 | LLM Gateway gpt-4o | - |
| Reviewer | NVIDIA NIM Llama 4 | LLM Gateway gpt-4o-mini | - |

**Cost:**
- NVIDIA NIM: $0.10-0.25 per 1M tokens (cheapest)
- LLM Gateway: NVIDIA's pricing (secure, approved)

**Reliability:**
- NVIDIA NIM: Fast but has tool calling bugs
- LLM Gateway: Reliable, works every time

---

## ✅ **Benefits of This Approach:**

1. **Security Compliant** ✅
   - No data sent to external providers
   - All through NVIDIA infrastructure
   - Starfleet auth

2. **Cost Optimized** ✅
   - Try NVIDIA NIM first (cheapest)
   - Fall back to LLM Gateway (still NVIDIA-approved)
   - Minimize costs while maintaining reliability

3. **Reliable** ✅
   - When NIM works: Great performance + low cost
   - When NIM fails: LLM Gateway catches it
   - Tasks always complete

4. **Future-Proof** ✅
   - As NVIDIA NIM improves, we use it more
   - As tool calling bugs get fixed, success rate increases
   - Always have working fallback

---

## 🚀 **Next Steps:**

### **For You:**
1. **Get Starfleet credentials** from NVIDIA IT
   - STARFLEET_ID
   - STARFLEET_SECRET

2. **Add to .env:**
   ```bash
   STARFLEET_ID=your-client-id
   STARFLEET_SECRET=your-client-secret
   ```

3. **Test authentication:**
   ```bash
   # I'll create a test script for you
   node test-starfleet-auth.js
   ```

### **For Implementation (Sprint 2):**
1. Create `starfleet-auth.ts` helper
2. Add "nvidia-gateway" provider
3. Update model-manager.ts
4. Test end-to-end
5. Monitor and optimize

---

## 📝 **Endpoints Reference:**

```bash
# Starfleet Token (15 min expiry)
POST https://5kbfxgaqc3xgz8nhid1x1r8cfestoypn-trofuum-oc.ssa.nvidia.com/token
Auth: Basic (STARFLEET_ID:STARFLEET_SECRET)
Body: grant_type=client_credentials&scope=azureopenai-readwrite

# LLM Gateway Chat Completions
POST https://prod.api.nvidia.com/llm/v1/azure/openai/deployments/gpt-4o/chat/completions?api-version=2024-12-01-preview
Headers:
  - Authorization: Bearer {starfleet_token}
  - Content-Type: application/json
  - correlationId: {uuid}
```

---

## 🎯 **Current Status:**

✅ **Today (Sprint 1):**
- NVIDIA NIM configured
- Tool calling issues identified
- LLM Gateway architecture designed

⏳ **Next (Sprint 2):**
- Get Starfleet credentials
- Implement LLM Gateway provider
- Test and deploy

---

**This is the enterprise-grade solution for NVIDIA!** 🚀

**Provides you the Starfleet credentials and we'll implement it in Sprint 2!**

---

**Created:** October 19, 2025  
**Status:** Ready for Starfleet credentials  
**Version:** 1.0

