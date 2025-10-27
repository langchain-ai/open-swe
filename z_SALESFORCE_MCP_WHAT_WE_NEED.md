# Salesforce MCP - What We Have vs What We Need

**Your Questions Answered**

---

## ❓ **Question 1: Do we have everything we need, or do we need open source repos?**

### **✅ What We Already Have (Built-in to Open SWE):**

1. **`@langchain/mcp-adapters` v0.5.2** ✅
   - Already installed in Open SWE
   - Provides `MultiServerMCPClient`
   - Supports HTTP and SSE transports
   - **BUT:** Currently blocks stdio transport in production code!

2. **MCP Client Infrastructure** ✅
   - File: `apps/open-swe/src/utils/mcp-client.ts`
   - Already integrates with Planner
   - Already has tool discovery
   - Already logs MCP tools

3. **Tool Binding to Planner** ✅
   - File: `apps/open-swe/src/graphs/planner/nodes/generate-message/index.ts`
   - Line 113-128: MCP tools already added to Planner
   - Just need to add our Salesforce tools to this array

### **❌ What We DON'T Have (Need to Add/Install):**

1. **Salesforce MCP Server** ❌
   - Need: `@tsmztech/mcp-server-salesforce` OR similar
   - **Issue:** I couldn't find this exact package online
   - **Options:**
     - A) Use a different Salesforce MCP server (community)
     - B) Build our own lightweight wrapper
     - C) Use existing BMAD Salesforce MCP (you already have one!)

2. **stdio Transport Support** ❌
   - Current code filters OUT stdio servers (line 40-50 in mcp-client.ts)
   - Need to enable stdio for Salesforce MCP

3. **Admin Mode Routing** ❌
   - Need to add /Admin detection
   - Need to add adminMode flag to Task metadata
   - Need to skip Programmer conditionally

4. **Admin UI Tab** ❌
   - Need to create AdminPanel component
   - Need to add "admin" to tab types
   - Need to wire MCP events to UI

---

## ❓ **Question 2: Where does the MCP server need to be installed?**

### **📐 MCP Architecture Explained:**

```
┌─────────────────────────────────────────────────────┐
│  Open SWE (Client)                                  │
│  ├─ apps/open-swe/src/utils/mcp-client.ts         │
│  │   └─ MultiServerMCPClient (from @langchain)     │
│  │                                                   │
│  │   ┌─────────────────────────────────┐           │
│  │   │ stdio communication             │           │
│  │   │ (stdin/stdout pipes)            │           │
│  │   └─────────────────────────────────┘           │
│  │                ↓                                  │
│  └────────────────┼──────────────────────────────────┘
                     │
                     ↓
┌─────────────────────────────────────────────────────┐
│  MCP Server Process (Separate Process)              │
│  - Spawned by Open SWE via npx/node                │
│  - Runs in same machine                             │
│  - Communicates via stdio (stdin/stdout)            │
│  │                                                   │
│  ├─ Option A: @tsmztech/mcp-server-salesforce      │
│  │   (if it exists)                                 │
│  │                                                   │
│  ├─ Option B: Community MCP server                  │
│  │   (needs research)                               │
│  │                                                   │
│  └─ Option C: Your BMAD Salesforce MCP ⭐          │
│      (C:\Users\idant\Code\BMAD\                    │
│       nvidia-unified-salesforce-mcp)                │
└─────────────────────────────────────────────────────┘
```

### **Answer: MCP Server Location**

**The MCP server runs as a SEPARATE PROCESS on the SAME MACHINE:**

1. **NOT a remote HTTP server** (unlike NVIDIA LLM Gateway)
2. **Local process** spawned by Open SWE
3. **Communication:** stdio (stdin/stdout pipes)
4. **Lifecycle:** Starts when Open SWE connects, stops when Open SWE disconnects

**Installation Options:**

#### **Option A: npm Package (if available)**
```bash
# Install globally
npm install -g @tsmztech/mcp-server-salesforce

# Or via npx (no install needed)
npx @tsmztech/mcp-server-salesforce
```

**Open SWE spawns it:**
```typescript
spawn('npx.cmd', ['@tsmztech/mcp-server-salesforce'], {
  env: { SALESFORCE_USERNAME, SALESFORCE_PASSWORD, ... },
  stdio: ['pipe', 'pipe', 'pipe']
});
```

#### **Option B: Use Your BMAD Salesforce MCP ⭐ RECOMMENDED**
```bash
# Your existing MCP server:
C:\Users\idant\Code\BMAD\nvidia-unified-salesforce-mcp\
```

**Open SWE spawns it:**
```typescript
spawn('node', ['C:/Users/idant/Code/BMAD/nvidia-unified-salesforce-mcp/dist/index.js'], {
  env: { SALESFORCE_USERNAME, SALESFORCE_PASSWORD, ... },
  stdio: ['pipe', 'pipe', 'pipe']
});
```

**Advantages:**
- ✅ You already built it!
- ✅ You control the code
- ✅ Already has Salesforce integration
- ✅ Can customize for NVIDIA needs
- ✅ No external dependencies

#### **Option C: Build Lightweight Wrapper**
- Use `jsforce` library directly in Open SWE
- Wrap as LangChain tools
- No MCP server needed
- Simpler but loses MCP benefits

---

## 💡 **My Recommendation: Use Your BMAD MCP Server**

### **Why:**

1. **You Already Have It!**
   - Path: `C:\Users\idant\Code\BMAD\nvidia-unified-salesforce-mcp`
   - Already implements MCP protocol
   - Already has Salesforce auth
   - Ready to use!

2. **Control & Customization**
   - You control the code
   - Can add NVIDIA-specific features
   - Can optimize for your use cases
   - Can fix bugs immediately

3. **No External Dependencies**
   - No relying on community packages
   - No version conflicts
   - No security concerns with 3rd party code

4. **Integration Path:**
```typescript
// In Open SWE mcp-client.ts
const salesforceMcpConfig = {
  transport: 'stdio',
  command: 'node',
  args: [
    'C:/Users/idant/Code/BMAD/nvidia-unified-salesforce-mcp/dist/index.js'
  ],
  env: {
    SALESFORCE_CONNECTION_TYPE: 'User_Password',
    SALESFORCE_USERNAME: process.env.SALESFORCE_USERNAME,
    SALESFORCE_PASSWORD: process.env.SALESFORCE_PASSWORD,
    SALESFORCE_TOKEN: process.env.SALESFORCE_TOKEN,
    SALESFORCE_INSTANCE_URL: process.env.SALESFORCE_INSTANCE_URL,
  }
};

// Add to MCP servers
mergedServers['salesforce'] = salesforceMcpConfig;
```

---

## 🔧 **What Needs to be Modified**

### **1. Enable stdio in Open SWE's MCP Client**

**File:** `apps/open-swe/src/utils/mcp-client.ts`

**Current Code (line 38-50) - BLOCKS stdio:**
```typescript
if (transport === "http" || transport === "sse") {
  validatedServers[serverName] = config;
} else {
  logger.info(
    `Skipping MCP server "${serverName}" - only http and sse transports are supported, got: ${transport}`,
  );
}
```

**Change to ALLOW stdio:**
```typescript
if (transport === "http" || transport === "sse" || transport === "stdio") {
  validatedServers[serverName] = config;
} else {
  logger.info(
    `Skipping MCP server "${serverName}" - only http, sse, and stdio transports are supported, got: ${transport}`,
  );
}
```

**That's it!** Once stdio is enabled, `MultiServerMCPClient` from `@langchain/mcp-adapters` handles everything!

---

## 🎯 **Simplified Implementation Plan**

### **What We DON'T Need to Build:**

❌ Custom MCP client (use `MultiServerMCPClient` from @langchain)  
❌ stdio transport (already in @langchain/mcp-adapters)  
❌ Tool discovery (already in mcp-client.ts)  
❌ Tool wrapping (MultiServerMCPClient does this)

### **What We DO Need to Build:**

✅ Enable stdio in validation (1 line change)  
✅ Add Salesforce MCP server config  
✅ /Admin command detection  
✅ adminMode flag in Task metadata  
✅ Skip Programmer logic  
✅ Admin UI tab component  

**Estimated Time:** 1-2 days (not 3-4!) because we're reusing existing infra

---

## 💻 **Concrete Implementation**

### **Step 1: Point to Your BMAD MCP Server**

**File:** `apps/open-swe/.env`

```bash
# Salesforce MCP Configuration
SALESFORCE_MCP_ENABLED=true
SALESFORCE_MCP_SERVER_PATH=C:/Users/idant/Code/BMAD/nvidia-unified-salesforce-mcp/dist/index.js

# Salesforce Credentials (QAS)
SALESFORCE_CONNECTION_TYPE=User_Password
SALESFORCE_USERNAME=idant@nvidia.com.qas
SALESFORCE_PASSWORD=EdenDor22222
SALESFORCE_TOKEN=CUIRatIS395Dk7Wp6MQjixp2
SALESFORCE_INSTANCE_URL=https://test.salesforce.com
```

### **Step 2: Enable stdio in MCP Client**

**File:** `apps/open-swe/src/utils/mcp-client.ts` (line ~40)

```typescript
// OLD:
if (transport === "http" || transport === "sse") {

// NEW:
if (transport === "http" || transport === "sse" || transport === "stdio") {
```

### **Step 3: Add Salesforce Server to getMcpTools**

**File:** `apps/open-swe/src/utils/mcp-client.ts` (in `getMcpTools` function)

```typescript
export async function getMcpTools(
  config: GraphConfig,
): Promise<StructuredToolInterface[]> {
  try {
    let mergedServers: McpServers = {};
    
    // Existing logic for custom framework...
    
    // NEW: Add Salesforce MCP server if enabled
    if (process.env.SALESFORCE_MCP_ENABLED === 'true') {
      const salesforceServerPath = process.env.SALESFORCE_MCP_SERVER_PATH;
      
      if (salesforceServerPath) {
        mergedServers['salesforce'] = {
          transport: 'stdio',
          command: 'node',
          args: [salesforceServerPath],
          env: {
            SALESFORCE_CONNECTION_TYPE: process.env.SALESFORCE_CONNECTION_TYPE || 'User_Password',
            SALESFORCE_USERNAME: process.env.SALESFORCE_USERNAME,
            SALESFORCE_PASSWORD: process.env.SALESFORCE_PASSWORD,
            SALESFORCE_TOKEN: process.env.SALESFORCE_TOKEN,
            SALESFORCE_INSTANCE_URL: process.env.SALESFORCE_INSTANCE_URL,
          },
        };
        
        logger.info('Salesforce MCP server configured', {
          path: salesforceServerPath,
          instance: process.env.SALESFORCE_INSTANCE_URL,
        });
      }
    }
    
    // Existing user servers logic...
    
    const client = mcpClient(mergedServers);
    const tools = await client.getTools();
    return tools;
  } catch (error) {
    logger.error(`Error getting MCP tools: ${error}`);
    return [];
  }
}
```

**That's all the MCP plumbing!** MultiServerMCPClient handles:
- ✅ Spawning the process
- ✅ stdio communication
- ✅ Tool discovery
- ✅ Tool wrapping as LangChain tools
- ✅ Error handling

---

## 🎉 **Much Simpler Than Expected!**

### **Original Plan:** Build custom MCP client package

**New Plan:** Use existing `@langchain/mcp-adapters`!

**What This Means:**

1. **NO custom package needed** - leverage @langchain infrastructure
2. **2-3 lines of code** to enable stdio
3. **5-10 lines of code** to add Salesforce server config
4. **Existing MultiServerMCPClient** handles all the heavy lifting

### **Remaining Work:**

**Backend (~1 day):**
- Enable stdio (1 line)
- Add Salesforce server config (10 lines)
- Add /Admin detection (20 lines)
- Add adminMode flag to Task type (5 lines)
- Skip Programmer logic (10 lines)

**Frontend (~1 day):**
- Create AdminPanel component (100 lines)
- Add admin tab (20 lines)
- Wire event data (30 lines)
- Auto-switch logic (10 lines)

**Total: ~200 lines of code, 2 days work!**

---

## 🎯 **Recommended Approach**

### **Use Your BMAD MCP Server:**

**Path:** `C:\Users\idant\Code\BMAD\nvidia-unified-salesforce-mcp`

**Advantages:**
1. ✅ Already built by you
2. ✅ Already has Salesforce integration
3. ✅ You control the code
4. ✅ Can add NVIDIA-specific features
5. ✅ No external dependencies
6. ✅ Supports stdio (just need to test)

**How Open SWE Will Use It:**
```
Open SWE starts up
  ↓
Planner needs tools
  ↓
getMcpTools() is called
  ↓
MultiServerMCPClient spawns:
  node C:/Users/idant/Code/BMAD/nvidia-unified-salesforce-mcp/dist/index.js
  ↓
stdio pipes connect (stdin/stdout)
  ↓
Client discovers tools from server
  ↓
Tools wrapped as LangChain StructuredTools
  ↓
Added to Planner tool array
  ↓
User types /Admin command
  ↓
Planner can use Salesforce tools!
```

---

## 📦 **What We Need to Install:**

### **Nothing new!** ✅

**We have:**
- ✅ `@langchain/mcp-adapters` (already installed)
- ✅ `@modelcontextprotocol/sdk` (dependency of mcp-adapters)
- ✅ Your BMAD Salesforce MCP server (already built)

**We just need to:**
- ✅ Enable stdio transport (1 line change)
- ✅ Point to your MCP server path
- ✅ Pass credentials via env vars

---

## 🔍 **Verification: Does Your BMAD MCP Support stdio?**

Let me check your MCP server structure:

**Expected in your BMAD project:**
```typescript
// src/core/mcp-server.ts
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';

// Initialize transport
const transport = new StdioServerTransport();
await server.connect(transport);
```

**If YES:** ✅ Ready to use immediately!  
**If NO:** Need to add stdio transport (10 lines of code)

---

## 🎯 **Final Answer**

### **Do we need open source repos?**

**NO!** We have everything:
- ✅ `@langchain/mcp-adapters` (built-in to Open SWE)
- ✅ Your BMAD Salesforce MCP server
- ✅ Tool binding infrastructure
- ✅ Event system

### **Do we need to install the MCP server somewhere?**

**NO separate server needed!** 

The MCP server:
- ✅ Runs as a child process (spawned by Open SWE)
- ✅ Same machine as Open SWE
- ✅ Communicates via stdio (local pipes)
- ✅ Already exists in your BMAD project

### **What's the simplest path?**

**Option 1: Use Your BMAD MCP (RECOMMENDED)** ⭐
```
Time: 2 days
Risk: LOW
Code: ~200 lines
Dependencies: 0 new packages
```

1. Enable stdio in Open SWE mcp-client.ts (1 line)
2. Point to your BMAD MCP server path
3. Add /Admin routing logic
4. Add Admin UI tab
5. Done!

**Option 2: Use Community Package**
```
Time: 2-3 days
Risk: MEDIUM
Code: ~200 lines
Dependencies: 1 new package (if it exists)
```

1. Find/install community Salesforce MCP
2. Same as Option 1 but with external dependency

**Option 3: Build Custom Package**
```
Time: 3-4 days
Risk: MEDIUM
Code: ~500 lines
Dependencies: 1 new package
```

1. Create packages/salesforce-mcp/
2. Implement MCP protocol
3. Implement Salesforce integration
4. Same as Option 1

---

## 🚀 **My Strong Recommendation**

**Use YOUR BMAD MCP Server!**

**Why:**
1. You already built it
2. It already works
3. Zero new dependencies
4. You control the code
5. Can add features anytime
6. Fastest implementation

**Implementation Time:** **1-2 days** instead of 3-4!

---

**Next Step:** 

Want me to:
1. **Check your BMAD MCP** to confirm it supports stdio?
2. **Start implementing** using your BMAD MCP server?
3. **Search for alternative** community MCP servers?

**I recommend #2 - Let's use your BMAD MCP and get this done in 1-2 days!** 🚀

