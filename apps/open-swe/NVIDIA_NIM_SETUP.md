# NVIDIA NIM Integration Setup Guide

**Integration Type:** Quick (OpenAI-Compatible)  
**Primary Model:** meta/llama-3.3-70b-instruct  
**Fallback Model:** qwen/qwen3-235b-a22b  
**Final Fallback:** Anthropic Claude  
**Created:** October 7, 2025

---

## 🎯 **Strategy:**

```
All requests → NVIDIA NIM (meta/llama-3.3-70b-instruct)
    ↓ (if fails)
Try → NVIDIA NIM (qwen/qwen3-235b-a22b)
    ↓ (if fails)
Fallback → Anthropic Claude
```

---

## 📋 **Step 1: Get NVIDIA API Key**

### Go to: https://build.nvidia.com/

1. **Sign in** with NVIDIA account
2. **Navigate to API Keys** section
3. **Generate new API key**
4. **Copy the key** (starts with `nvapi-`)

---

## 🔧 **Step 2: Add to Environment Variables**

### **File:** `apps/open-swe/.env`

Add these lines to your `.env` file:

```bash
# NVIDIA NIM Configuration
NVIDIA_NIM_API_KEY=nvapi-YOUR_KEY_HERE
NVIDIA_NIM_BASE_URL=https://integrate.api.nvidia.com/v1
NVIDIA_NIM_PRIMARY_MODEL=meta/llama-3.3-70b-instruct
NVIDIA_NIM_FALLBACK_MODEL=qwen/qwen3-235b-a22b
```

**Full .env structure:**
```bash
# Existing keys
ANTHROPIC_API_KEY=your_anthropic_key
OPENAI_API_KEY=your_openai_key
LANGSMITH_API_KEY=your_langsmith_key
GITHUB_APP_ID=your_app_id
GITHUB_CLIENT_ID=your_client_id
GITHUB_CLIENT_SECRET=your_secret
GITHUB_PRIVATE_KEY=your_key
DAYTONA_API_KEY=your_daytona_key

# NEW: NVIDIA NIM Configuration
NVIDIA_NIM_API_KEY=nvapi-YOUR_KEY_HERE
NVIDIA_NIM_BASE_URL=https://integrate.api.nvidia.com/v1
NVIDIA_NIM_PRIMARY_MODEL=meta/llama-3.3-70b-instruct
NVIDIA_NIM_FALLBACK_MODEL=qwen/qwen3-235b-a22b

# Other settings
SECRETS_ENCRYPTION_KEY=your_encryption_key
```

---

## 🔨 **Step 3: Implementation (Already Done Below)**

The following files have been updated to support NVIDIA NIM:

### **Files Modified:**

1. ✅ `src/utils/llms/model-manager.ts` - Added NVIDIA NIM as provider
2. ✅ `packages/shared/src/open-swe/llm-task.ts` - Updated default models
3. ✅ Environment configuration ready

---

## 🚀 **Step 4: Testing**

Once you add your API key:

### **Test Command:**
```bash
# Restart the server
# Press Ctrl+C in terminal
yarn dev
```

### **In the UI:**
1. Open http://localhost:3000
2. Create a new task
3. Check logs for: "Using NVIDIA NIM model: meta/llama-3.3-70b-instruct"
4. If successful, you'll see responses from NVIDIA NIM!

---

## 📊 **Expected Cost Savings:**

| Provider | Cost per 1M tokens | Savings |
|----------|-------------------|---------|
| Anthropic Claude | ~$3-15 | Baseline |
| NVIDIA NIM | ~$0.10-0.50 | **80-90%** ✅ |

---

## 🔍 **Troubleshooting:**

### **If NVIDIA NIM fails:**
- Check API key is correct (starts with `nvapi-`)
- Verify you have credits at build.nvidia.com
- Check logs for error messages
- System will auto-fallback to Anthropic

### **Check Logs:**
```bash
# In terminal where yarn dev is running
# Look for:
info: Initializing model { provider: 'openai', modelName: 'meta/llama-3.3-70b-instruct' }
```

---

## ⚡ **Quick Start:**

1. **Get API key** from https://build.nvidia.com/
2. **Add to `.env`:**
   ```bash
   NVIDIA_NIM_API_KEY=nvapi-YOUR_KEY_HERE
   ```
3. **Restart server:** `Ctrl+C` then `yarn dev`
4. **Test in UI** - Create a task and watch it use NVIDIA NIM!

---

## 🎉 **Success Indicators:**

✅ Server starts without errors  
✅ Logs show "Using NVIDIA NIM"  
✅ Responses are fast and accurate  
✅ Cost reduces by 80-90%  
✅ If NIM fails, automatically uses Anthropic

---

**Ready to add your NVIDIA API key when you have it!** 🚀



