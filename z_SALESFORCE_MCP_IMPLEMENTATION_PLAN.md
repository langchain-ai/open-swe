# Salesforce MCP Integration - Implementation Plan

**Date:** October 27, 2025  
**Project:** NVIDIA Open SWE + Salesforce MCP Integration  
**Target:** QAS Environment, Windows + Yarn

---

## 📋 **Analysis Complete - Architecture Understood**

### **What I Found:**

✅ **Existing MCP Infrastructure:**
- Open SWE already has MCP client support via `@langchain/mcp-adapters`
- Currently supports HTTP/SSE transports (stdio not enabled in production)
- MCP tools are added to Planner in `generate-message/index.ts` line 113-128
- Tools come from `getMcpTools(config)` function

✅ **Routing Architecture:**
- Manager classifies messages via `classify-message/index.ts`
- Routes can be: `start_planner`, `continue_conversation`, `error`
- Planner starts via `start-planner.ts` node

✅ **UI Structure:**
- Thread view has tabs: "planner" | "programmer" (line 99 of thread-view.tsx)
- Tabs system uses shadcn/ui Tabs component
- Easy to add a third "admin" tab

---

## 🎯 **Implementation Plan - 6 Phases**

### **Phase 1: Salesforce MCP Plugin Package (Isolated)**

Create: `packages/salesforce-mcp/`

```
packages/salesforce-mcp/
├── src/
│   ├── client.ts          # MCP client for Salesforce (stdio)
│   ├── tools.ts           # Tool discovery and wrapping
│   ├── events.ts          # Event bus for Admin UI
│   ├── auth.ts            # Username/password/token auth
│   ├── safety.ts          # Redaction + chunking
│   └── index.ts           # Public exports
├── package.json
└── tsconfig.json
```

**Key Features:**
```typescript
// client.ts
export class SalesforceMCPClient {
  async connect(timeout = 1500): Promise<void> {
    // Windows-safe: use npx.cmd on Windows
    const command = process.platform === 'win32' ? 'npx.cmd' : 'npx';
    const args = ['@tsmztech/mcp-server-salesforce'];
    
    // Spawn with timeout
    // Emit connection events
  }
  
  async disconnect(): Promise<void> {
    // Clean shutdown
  }
}

// tools.ts
export async function discoverTools(): Promise<Tool[]> {
  // Get all tools matching pattern '*'
  // Wrap each with timing + redaction
  // Set tool timeout to 10s
}

// safety.ts  
export function redactSensitive(data: any): any {
  // Redact: token, secret, password, cookie, authorization
}

export function chunkDML(records: any[], chunkSize = 50): any[][] {
  // Split DML operations into chunks of 50
}

// events.ts
export class McpEventBus extends EventEmitter {
  // server.connected, server.disconnected
  // tool.called, tool.completed, tool.failed
}
```

---

### **Phase 2: Wire MCP into Planner**

**File:** `apps/open-swe/src/utils/mcp-client.ts`

**Add Salesforce MCP client:**
```typescript
import { SalesforceMCPClient } from '@openswe/salesforce-mcp';

// Singleton for Salesforce MCP
let salesforceMcpClient: SalesforceMCPClient | null = null;

export async function getSalesforceMcpTools(
  config: GraphConfig  
): Promise<StructuredToolInterface[]> {
  try {
    if (!salesforceMcpClient) {
      salesforceMcpClient = new SalesforceMCPClient({
        username: process.env.SALESFORCE_USERNAME,
        password: process.env.SALESFORCE_PASSWORD,
        securityToken: process.env.SALESFORCE_SECURITY_TOKEN,
        instanceUrl: process.env.SALESFORCE_INSTANCE_URL,
      });
      
      await salesforceMcpClient.connect(1500); // 1.5s timeout
    }
    
    const tools = await salesforceMcpClient.discoverTools();
    return tools;
  } catch (error) {
    logger.error('Salesforce MCP connection failed', { error });
    // Graceful degradation - return empty array
    return [];
  }
}
```

**File:** `apps/open-swe/src/graphs/planner/nodes/generate-message/index.ts`

**Update line 113-128:**
```typescript
const mcpTools = await getMcpTools(config);
const salesforceMcpTools = await getSalesforceMcpTools(config); // NEW!

const tools = [
  createGrepTool(state, config),
  createShellTool(state, config),
  createViewTool(state, config),
  createScratchpadTool("..."),
  createGetURLContentTool(state),
  createSearchDocumentForTool(state, config),
  ...mcpTools,
  ...salesforceMcpTools, // NEW!
];

logger.info(
  `MCP tools added to Planner: ${mcpTools.map((t) => t.name).join(", ")}`,
);
logger.info(
  `Salesforce MCP tools added: ${salesforceMcpTools.map((t) => t.name).join(", ")}`,
);
```

---

### **Phase 3: /Admin Command Handling**

**File:** `apps/open-swe/src/graphs/manager/nodes/classify-message/index.ts`

**Add admin mode detection (line ~60):**
```typescript
export async function classifyMessage(
  state: ManagerGraphState,
  config: GraphConfig,
): Promise<Command> {
  const userMessage = state.messages.findLast(isHumanMessage);
  if (!userMessage) {
    throw new Error("No human message found.");
  }

  // NEW: Check for /Admin command
  const messageText = getMessageContentString(userMessage.content);
  const isAdminCommand = messageText.trim().startsWith('/Admin');
  
  if (isAdminCommand) {
    // Strip /Admin prefix and set admin mode
    const actualCommand = messageText.trim().substring(6).trim(); // Remove "/Admin"
    
    logger.info('Admin command detected', {
      original: messageText,
      stripped: actualCommand,
    });
    
    // Update the message content (strip /Admin)
    const updatedMessage = new HumanMessage({
      content: actualCommand,
      id: userMessage.id,
    });
    
    // Continue to planner but mark as admin mode
    // (We'll add task.meta.adminMode in Phase 4)
  }
  
  // Rest of existing routing logic...
}
```

**Add to routing schemas (schemas.ts):**
```typescript
export const ADMIN_CLASSIFICATION_SCHEMA = BASE_CLASSIFICATION_SCHEMA.extend({
  route: z.enum([
    "start_admin_planner", // NEW route for admin mode
    "continue_conversation",
    "error",
  ]),
});
```

---

### **Phase 4: Admin Mode State**

**File:** `packages/shared/src/open-swe/types.ts`

**Add to Task type (around line 79):**
```typescript
export type Task = {
  id: string;
  taskIndex: number;
  request: string;
  title: string;
  createdAt: number;
  completed: boolean;
  completedAt?: number;
  summary?: string;
  planRevisions: PlanRevision[];
  activeRevisionIndex: number;
  parentTaskId?: string;
  pullRequestNumber?: number;
  
  // NEW: Admin mode metadata
  meta?: {
    adminMode?: boolean;
    mcpServerStatus?: 'connected' | 'disconnected' | 'error';
    toolCallHistory?: ToolCallRecord[];
  };
};

// NEW: Tool call tracking for Admin UI
export type ToolCallRecord = {
  timestamp: number;
  toolName: string;
  status: 'success' | 'error';
  durationMs: number;
  args?: any; // Redacted
  error?: string; // Redacted
};
```

**File:** `apps/open-swe/src/graphs/manager/nodes/start-planner.ts`

**Pass admin mode to planner (line ~63):**
```typescript
const runInput: PlannerGraphUpdate = {
  githubIssueId: state.githubIssueId,
  targetRepository: state.targetRepository,
  taskPlan: state.taskPlan,
  branchName: state.branchName ?? getBranchName(config),
  autoAcceptPlan: state.autoAcceptPlan,
  
  // NEW: Pass admin mode flag
  ...(state.taskPlan.tasks[state.taskPlan.activeTaskIndex]?.meta?.adminMode && {
    adminMode: true,
  }),
  
  ...(followupMessage || localMode ? { messages: [followupMessage] } : {}),
  ...(!shouldCreateIssue(config) && followupMessage
    ? { internalMessages: [followupMessage] }
    : {}),
};
```

---

### **Phase 5: Skip Programmer in Admin Mode**

**File:** `apps/open-swe/src/graphs/manager/index.ts`

**Update routing logic to skip Programmer when admin mode:**
```typescript
// After planner completes, check if admin mode
const shouldSkipProgrammer = (state: ManagerGraphState) => {
  const activeTask = state.taskPlan.tasks[state.taskPlan.activeTaskIndex];
  return activeTask?.meta?.adminMode === true;
};

// In graph definition:
.addConditionalEdges('planner', (state) => {
  if (shouldSkipProgrammer(state)) {
    return END; // Don't start programmer in admin mode
  }
  return 'programmer'; // Normal flow
})
```

---

### **Phase 6: Admin UI Tab**

**File:** `apps/web/src/components/v2/thread-view.tsx`

**Update line 99 to add "admin" tab:**
```typescript
const [selectedTab, setSelectedTab] = useState<"planner" | "programmer" | "admin">(
  "planner",
);

// Add state for admin data
const [mcpServerStatus, setMcpServerStatus] = useState<'connected' | 'disconnected'>('disconnected');
const [toolCallHistory, setToolCallHistory] = useState<ToolCallRecord[]>([]);
```

**Add Admin tab in JSX (around line 300+):**
```tsx
<Tabs value={selectedTab} onValueChange={(v) => setSelectedTab(v as any)}>
  <TabsList className="grid w-full grid-cols-3"> {/* Changed from grid-cols-2 */}
    <TabsTrigger value="planner">Planner</TabsTrigger>
    <TabsTrigger value="programmer">Programmer</TabsTrigger>
    <TabsTrigger value="admin">Admin</TabsTrigger> {/* NEW */}
  </TabsList>
  
  <TabsContent value="planner">
    {/* Existing planner content */}
  </TabsContent>
  
  <TabsContent value="programmer">
    {/* Existing programmer content */}
  </TabsContent>
  
  <TabsContent value="admin"> {/* NEW */}
    <AdminPanel 
      serverStatus={mcpServerStatus}
      toolCallHistory={toolCallHistory}
    />
  </TabsContent>
</Tabs>
```

**Create:** `apps/web/src/components/v2/admin-panel.tsx`

```tsx
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

interface AdminPanelProps {
  serverStatus: 'connected' | 'disconnected' | 'error';
  toolCallHistory: ToolCallRecord[];
}

export function AdminPanel({ serverStatus, toolCallHistory }: AdminPanelProps) {
  return (
    <div className="space-y-4">
      {/* Server Status */}
      <Card>
        <CardHeader>
          <CardTitle>MCP Server Status</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-2">
            <Badge variant={serverStatus === 'connected' ? 'default' : 'destructive'}>
              {serverStatus}
            </Badge>
            <span className="text-sm text-muted-foreground">
              Salesforce MCP Server (@tsmztech/mcp-server-salesforce)
            </span>
          </div>
          <div className="mt-2 text-sm">
            Tools Available: {toolCallHistory.length > 0 ? 'Yes' : 'Pending'}
          </div>
        </CardContent>
      </Card>

      {/* Recent Tool Calls */}
      <Card>
        <CardHeader>
          <CardTitle>Recent Tool Calls</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-2">
            {toolCallHistory.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No tool calls yet
              </p>
            ) : (
              toolCallHistory.map((call, i) => (
                <div key={i} className="border-l-2 border-blue-500 pl-3 py-2">
                  <div className="flex items-center justify-between">
                    <span className="font-mono text-sm">{call.toolName}</span>
                    <Badge variant={call.status === 'success' ? 'default' : 'destructive'}>
                      {call.status}
                    </Badge>
                  </div>
                  <div className="text-xs text-muted-foreground mt-1">
                    {new Date(call.timestamp).toLocaleTimeString()} • {call.durationMs}ms
                  </div>
                  {call.error && (
                    <div className="text-xs text-red-500 mt-1">
                      {call.error.substring(0, 100)}...
                    </div>
                  )}
                </div>
              ))
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
```

---

## 🔧 **Environment Variables**

**Add to `.env`:**
```bash
# Salesforce MCP Configuration (QAS)
SALESFORCE_MCP_ENABLED=true
SALESFORCE_INSTANCE_URL=https://nvidia--qas.sandbox.my.salesforce.com
SALESFORCE_USERNAME=your-username@nvidia.com.qas
SALESFORCE_PASSWORD=your-password
SALESFORCE_SECURITY_TOKEN=your-security-token

# MCP Server Path (Community Server)
SALESFORCE_MCP_SERVER=@tsmztech/mcp-server-salesforce

# Safety Settings
SALESFORCE_MCP_TIMEOUT=10000  # 10s per tool call
SALESFORCE_MCP_CONNECT_TIMEOUT=1500 # 1.5s connection
SALESFORCE_MCP_DML_CHUNK_SIZE=50  # Batch DML operations
```

---

## ❓ **Questions for You**

Before I proceed with implementation, please answer:

### **1. Salesforce QAS Credentials:**
- Do you have the QAS org credentials (username/password/security token)?
- What's the QAS instance URL?

### **2. MCP Server Installation:**
- Should we install `@tsmztech/mcp-server-salesforce` as a dependency?
- Or assume it's globally installed via `npx`?

### **3. Admin Tab Behavior:**
- When user types `/Admin show my cases`, should the Admin tab:
  - A) Auto-switch after plan approval?
  - B) Require manual click to Admin tab?
  - C) Show Admin tab side-by-side with Planner?

### **4. DML Permissions:**
- In QAS, which objects are safe for DML?
  - Case, Task, Event (safe?)
  - Account, Contact (risky?)
  - Custom objects (depends?)
- Should we have a whitelist or allow all?

### **5. Fallback Behavior:**
- If Salesforce MCP fails to connect:
  - A) Fail the task immediately?
  - B) Continue without Salesforce tools (degraded mode)?
  - C) Show warning but let Planner work?

### **6. Tool Discovery:**
- Pattern matching `*` means ALL Salesforce tools will be available
- Do you want to limit to specific tools? (e.g., only query*, describe*, update*)

---

## ⚠️ **Risk Assessment & Safety**

### **🟢 LOW RISK (Safe to Implement):**

1. ✅ **MCP Client Package (Isolated)**
   - New package, no existing code modified
   - Can be disabled via env var
   - No breaking changes

2. ✅ **Tool Discovery (Read-Only)**
   - Query tools are safe
   - Describe tools are safe
   - No data modification

3. ✅ **Admin UI Tab**
   - Additive change only
   - Existing tabs unaffected
   - Can be hidden if not in admin mode

### **🟡 MEDIUM RISK (Needs Testing):**

4. ⚠️ **Routing Logic (/Admin Detection)**
   - Modifies classify-message node
   - Could affect normal routing if regex is wrong
   - **Mitigation:** Use exact prefix match `/Admin ` (with space)
   - **Test:** Ensure normal messages still work

5. ⚠️ **Skipping Programmer**
   - Changes manager graph edges
   - Could break normal flow if condition is wrong
   - **Mitigation:** Only skip when `adminMode === true`
   - **Test:** Ensure programmer still runs for normal tasks

6. ⚠️ **Stdio MCP Client**
   - Spawns external process (npx)
   - Could hang if server crashes
   - **Mitigation:** 1.5s connection timeout, process cleanup
   - **Test:** Handle server not installed gracefully

### **🔴 HIGH RISK (Needs Approval & Safety Rails):**

7. ⛔ **DML Operations in QAS**
   - Can modify/delete Salesforce data
   - Batch updates could affect many records
   - **Mitigation:**
     - Chunk to 50 records max
     - Log every DML operation
     - Redact sensitive fields in logs
     - Consider approval workflow (future)
   - **Recommendation:** Start with read-only, add DML later

8. ⛔ **Credential Handling**
   - Username/password in .env
   - Security token in plaintext
   - **Mitigation:**
     - Use SECRETS_ENCRYPTION_KEY (existing)
     - Don't log credentials
     - Redact in tool call logs
   - **Recommendation:** Move to OAuth 2.0 later (as planned)

---

## 🏗️ **Recommended Implementation Order**

### **Sprint 1: Read-Only Foundation (1-2 days)**

**Goal:** Get Salesforce MCP connected with query tools only

**Tasks:**
1. Create `packages/salesforce-mcp/` package
2. Implement MCP client with stdio (Windows-safe)
3. Tool discovery (filter to query* and describe* only)
4. Wire into Planner tools
5. Add .env configuration
6. Test with `/Admin SELECT Name FROM Account LIMIT 3`

**Risk:** 🟢 LOW - No data modification

---

### **Sprint 2: Admin UI Tab (1 day)**

**Goal:** Add Admin tab to show MCP status

**Tasks:**
1. Create AdminPanel component
2. Add "admin" to tab types
3. Wire MCP events to UI state
4. Show server status + tool calls
5. Auto-switch to Admin tab on /Admin commands

**Risk:** 🟢 LOW - UI only

---

### **Sprint 3: /Admin Command Routing (1 day)**

**Goal:** Detect /Admin and skip Programmer

**Tasks:**
1. Add /Admin detection in classify-message
2. Set task.meta.adminMode flag
3. Update manager graph to skip Programmer
4. Test routing with various commands

**Risk:** 🟡 MEDIUM - Routing logic changes

**Testing:**
- `/Admin show cases` → Planner only, Admin tab
- `show cases` (no /Admin) → Planner → Programmer (normal)

---

### **Sprint 4: DML Support (1-2 days) - OPTIONAL/LATER**

**Goal:** Enable safe DML operations

**Tasks:**
1. Add DML tools (update*, insert*, delete*)
2. Implement 50-record chunking
3. Add per-record error handling
4. Extensive logging
5. Test in QAS only

**Risk:** 🔴 HIGH - Data modification

**Recommendation:** Start with Sprint 1-3, add DML only after approval

---

## 💡 **Suggestions & Best Practices**

### **Suggestion 1: Start Read-Only**
**Why:** Safer, faster to implement, prove the architecture works  
**What:** Only enable query* and describe* tools in Sprint 1  
**When:** Add DML after read-only is stable

### **Suggestion 2: Graceful Degradation**
**Why:** Salesforce MCP might be down/unavailable  
**What:** If MCP connect fails, log warning but let Planner continue  
**How:** Return empty array from `getSalesforceMcpTools()` on error

### **Suggestion 3: Separate Package**
**Why:** Isolation, easier to disable, no risk to core Open SWE  
**What:** `packages/salesforce-mcp/` as standalone package  
**Benefit:** Can be disabled with env var, doesn't affect main flow

### **Suggestion 4: Event-Driven UI**
**Why:** Decouple backend from frontend  
**What:** Use event bus for MCP status/tool calls  
**How:** Backend emits events → Admin UI subscribes → Real-time updates

### **Suggestion 5: Tool Call Logging**
**Why:** Debugging, audit trail, visibility  
**What:** Log every MCP tool call with timing + args  
**Safety:** Auto-redact sensitive fields (token, password, etc.)

---

## 🛡️ **What Could Break?**

### **Potential Issues:**

1. **npx.cmd not found on Windows**
   - **Risk:** MCP server won't start
   - **Fix:** Check for npx.cmd vs npx, fall back gracefully

2. **stdio communication hang**
   - **Risk:** Planner waits forever
   - **Fix:** 1.5s connection timeout, 10s tool timeout

3. **Routing interferes with normal flow**
   - **Risk:** Non-admin commands might be misclassified
   - **Fix:** Use exact `/Admin ` prefix with space, case-sensitive

4. **Programmer skipped incorrectly**
   - **Risk:** Normal tasks don't execute code
   - **Fix:** Only skip if `adminMode === true` AND plan approved

5. **UI tabs break**
   - **Risk:** Existing Planner/Programmer tabs malfunction
   - **Fix:** Additive changes only, default to "planner" tab

6. **MCP server crashes**
   - **Risk:** Planner tool calls fail
   - **Fix:** Graceful error handling, surface in Admin tab

7. **DML affects wrong records**
   - **Risk:** Data corruption in QAS
   - **Fix:** Start read-only; add DML later with approval flow

---

## 📋 **Implementation Checklist**

**Before Starting:**
- [ ] Get Salesforce QAS credentials
- [ ] Confirm tool whitelist (all or subset?)
- [ ] Decide on DML permissions (now or later?)
- [ ] Choose Admin tab behavior (auto-switch or manual?)

**Phase 1: Package Creation**
- [ ] Create `packages/salesforce-mcp/` structure
- [ ] Implement Windows-safe MCP client
- [ ] Add tool discovery (pattern: query*, describe*)
- [ ] Test connection locally

**Phase 2: Planner Integration**
- [ ] Wire `getSalesforceMcpTools()` into Planner
- [ ] Test tools appear in Planner
- [ ] Verify graceful failure

**Phase 3: /Admin Routing**
- [ ] Add /Admin detection
- [ ] Set adminMode flag
- [ ] Skip Programmer logic
- [ ] Test routing

**Phase 4: Admin UI**
- [ ] Create AdminPanel component
- [ ] Add admin tab
- [ ] Show server status
- [ ] Show tool call history

**Phase 5: Testing**
- [ ] `/Admin SELECT Name FROM Account LIMIT 3`
- [ ] `/Admin show my open cases`
- [ ] Verify normal tasks still work
- [ ] Verify Programmer not invoked for /Admin

**Phase 6: DML (Optional)**
- [ ] Add DML tools
- [ ] Implement chunking
- [ ] Test in QAS
- [ ] Add approval workflow

---

## 🎯 **Recommended Approach**

**Option A: Minimal Viable Integration (1-2 days)**
- ✅ Read-only Salesforce tools
- ✅ /Admin command detection
- ✅ Basic Admin UI tab
- ✅ Skip Programmer in admin mode
- ❌ No DML yet (safer)

**Option B: Full Implementation (3-4 days)**
- ✅ All Salesforce tools (query + DML)
- ✅ /Admin command with full routing
- ✅ Rich Admin UI with real-time updates
- ✅ DML chunking and safety
- ⚠️ Higher risk but complete feature

**My Recommendation: Start with Option A**
- Prove the architecture works
- Less risk of breaking existing functionality
- Can add DML in Sprint 2 after validation

---

## 📞 **Next Steps - Your Input Needed**

Please provide:

1. **QAS Credentials:**
   - Username
   - Password
   - Security Token
   - Instance URL

2. **Tool Scope Decision:**
   - A) Start read-only (query*, describe*) - **RECOMMENDED**
   - B) Enable all tools including DML

3. **Admin Tab Behavior:**
   - A) Auto-switch to Admin after plan approval
   - B) User manually clicks Admin tab

4. **Implementation Pace:**
   - A) Quick MVP (read-only, 1-2 days) - **RECOMMENDED**
   - B) Full feature (with DML, 3-4 days)

5. **Approval:**
   - Do you approve this architecture?
   - Any concerns or changes needed?

---

Once you answer these questions, I'll start implementing immediately! 🚀

