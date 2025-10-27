# Environment Variables Setup for NVIDIA NIM

**File Location:** `apps/open-swe/.env`

---

## 🔑 **Add These to Your .env File:**

Open your `.env` file and add the following lines:

```bash
# ===================================
# NVIDIA NIM Configuration (NEW!)
# ===================================
NVIDIA_NIM_API_KEY=nvapi-YOUR_KEY_HERE
NVIDIA_NIM_BASE_URL=https://integrate.api.nvidia.com/v1
NVIDIA_NIM_PRIMARY_MODEL=meta/llama-3.3-70b-instruct
NVIDIA_NIM_FALLBACK_MODEL=qwen/qwen3-235b-a22b
```

---

## 📋 **Complete .env Structure:**

Your `.env` file should look like this:

```bash
# ===================================
# NVIDIA NIM Configuration
# Get API key from: https://build.nvidia.com/
# ===================================
NVIDIA_NIM_API_KEY=nvapi-YOUR_KEY_HERE
NVIDIA_NIM_BASE_URL=https://integrate.api.nvidia.com/v1
NVIDIA_NIM_PRIMARY_MODEL=meta/llama-3.3-70b-instruct
NVIDIA_NIM_FALLBACK_MODEL=qwen/qwen3-235b-a22b

# ===================================
# LLM API Keys (Fallbacks)
# ===================================
ANTHROPIC_API_KEY=your_anthropic_key_here
OPENAI_API_KEY=your_openai_key_here
GOOGLE_API_KEY=your_google_key_here

# ===================================
# GitHub App Configuration
# ===================================
GITHUB_APP_ID=your_github_app_id
GITHUB_CLIENT_ID=your_github_client_id
GITHUB_CLIENT_SECRET=your_github_client_secret
GITHUB_PRIVATE_KEY=your_private_key_single_line_with_\n_escapes

# ===================================
# Daytona Sandboxes
# ===================================
DAYTONA_API_KEY=your_daytona_key_here

# ===================================
# LangSmith Tracing (Optional)
# ===================================
LANGSMITH_API_KEY=your_langsmith_key_here
LANGSMITH_PROJECT=open-swe-nvidia

# ===================================
# Security
# ===================================
SECRETS_ENCRYPTION_KEY=your_32_character_encryption_key

# ===================================
# Database (Optional - for production)
# ===================================
DATABASE_URL=postgresql://user:password@localhost:5432/openswe
```

---

## 🚀 **Steps to Activate NVIDIA NIM:**

### **1. Get Your API Key**
- Go to: https://build.nvidia.com/
- Sign in with NVIDIA account
- Navigate to "API Keys" or "Keys" section
- Click "Generate API Key"
- Copy the key (starts with `nvapi-`)

### **2. Add to .env File**
```bash
# Edit your .env file
code apps/open-swe/.env

# Or use notepad
notepad apps/open-swe/.env

# Add the NVIDIA NIM section from above
```

### **3. Restart Server**
```bash
# In terminal where yarn dev is running:
# Press Ctrl+C to stop

# Then restart:
cd C:\Users\idant\Code\open-swe\open-swe
yarn dev
```

### **4. Verify**
- Watch the logs when server starts
- Look for: "Initializing model { provider: 'nvidia-nim', ... }"
- Create a test task in the UI
- Check logs show NVIDIA NIM is being used

---

## 🎯 **How It Works:**

```
Request comes in
    ↓
Try: NVIDIA NIM (meta/llama-3.3-70b-instruct)
    ↓ (if fails after 2 attempts)
Try: NVIDIA NIM (qwen/qwen3-235b-a22b)
    ↓ (if fails after 2 attempts)
Fallback: Anthropic Claude (your original provider)
```

---

## 💰 **Expected Cost Savings:**

| Task | Provider | Cost per 1M tokens | Status |
|------|----------|-------------------|--------|
| Planning | NVIDIA NIM | ~$0.25 | Primary ✅ |
| Programming | NVIDIA NIM | ~$0.25 | Primary ✅ |
| Reviewing | NVIDIA NIM | ~$0.10 | Primary ✅ |
| Routing | NVIDIA NIM | ~$0.10 | Primary ✅ |
| Fallback | Anthropic | ~$3-15 | If NIM fails |

**Expected savings: 80-90%** 🎉

---

## 🔍 **Testing:**

### **Test 1: Check Configuration**
```bash
# After adding API key, check logs:
# Should see:
info: Initialized { 
  config: {...},
  fallbackOrder: ['nvidia-nim', 'openai', 'anthropic', 'google-genai']
}
```

### **Test 2: Create Simple Task**
1. Go to http://localhost:3000
2. Create task: "Add a hello world function to test.js"
3. Watch logs for:
   ```
   Initializing model { provider: 'nvidia-nim', modelName: 'meta/llama-3.3-70b-instruct' }
   ```

### **Test 3: Monitor Costs**
- Check NVIDIA NIM dashboard at build.nvidia.com
- Compare costs with previous Anthropic usage
- Should see 80-90% reduction

---

## ⚠️ **Important Notes:**

1. **API Key Format:** Must start with `nvapi-`
2. **Base URL:** Must be exactly `https://integrate.api.nvidia.com/v1`
3. **Model Names:** Must match NVIDIA NIM catalog exactly
4. **Restart Required:** After adding API key, restart server
5. **Fallback Active:** If NIM fails, automatically uses Anthropic

---

## 📞 **Troubleshooting:**

### **Error: "Unknown provider: nvidia-nim"**
- TypeScript error after adding provider
- Solution: Restart TypeScript server in VS Code (Cmd/Ctrl+Shift+P → "Restart TS Server")

### **Error: "No API key found"**
- NVIDIA_NIM_API_KEY not in .env
- Solution: Add the key and restart server

### **Error: "Model not found"**
- Model name typo
- Solution: Check exact model name at https://build.nvidia.com/

### **Requests Still Using Anthropic:**
- NVIDIA NIM API key not set
- Solution: Add key to .env, restart server

---

**Once you have your NVIDIA API key, just add it to `.env` and restart!** 🚀



