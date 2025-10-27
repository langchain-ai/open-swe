# Salesforce MCP Integration - Detailed Implementation Plan

**Date:** October 27, 2025  
**Scope:** Full implementation with DML support  
**Environment:** QAS (test.salesforce.com)  
**Strategy:** Describe → Query → DML workflow

---

## 🔑 **Credentials (QAS)**

```bash
SALESFORCE_CONNECTION_TYPE=User_Password
SALESFORCE_USERNAME=idant@nvidia.com.qas
SALESFORCE_PASSWORD=EdenDor22222
SALESFORCE_TOKEN=CUIRatIS395Dk7Wp6MQjixp2
SALESFORCE_INSTANCE_URL=https://test.salesforce.com
```

---

## 🎯 **Implementation Strategy**

### **Tool Workflow:**
```
1. Describe object(s) → Get schema, fields, relationships
2. Query data → Read records with proper fields
3. DML operations → Update/Insert/Delete in 50-record chunks
```

### **Safety Approach:**
```
✅ DML included from start
✅ Chunked to 50 records max per operation
✅ Per-record error handling
✅ All DML logged with redaction
✅ QAS environment only (safe sandbox)
```

### **Admin Tab:**
```
Nice-to-have: Auto-switch after plan approval
Implementation: Make it configurable
Risk: LOW - UI only, doesn't affect core logic
Decision: IMPLEMENT with feature flag
```

---

## 🏗️ **Implementation Plan - 4 Phases**

### **Phase 1: Salesforce MCP Package**

**Create:** `packages/salesforce-mcp/`

#### **File Structure:**
```
packages/salesforce-mcp/
├── package.json
├── tsconfig.json
├── src/
│   ├── index.ts                 # Public exports
│   ├── client.ts                # MCP client (stdio, Windows-safe)
│   ├── tools.ts                 # Tool discovery & wrapping
│   ├── auth.ts                  # Username/password/token auth
│   ├── safety.ts                # Redaction + DML chunking
│   ├── events.ts                # Event bus for Admin UI
│   └── types.ts                 # TypeScript types
└── README.md
```

#### **Key Files:**

**1. `package.json`:**
```json
{
  "name": "@openswe/salesforce-mcp",
  "version": "1.0.0",
  "type": "module",
  "main": "./dist/index.js",
  "types": "./dist/index.d.ts",
  "scripts": {
    "build": "tsc",
    "dev": "tsc --watch"
  },
  "dependencies": {
    "@modelcontextprotocol/sdk": "latest",
    "eventemitter3": "^5.0.1",
    "zod": "^3.25.32"
  },
  "devDependencies": {
    "typescript": "~5.7.2"
  }
}
```

**2. `client.ts` (Windows-safe stdio):**
```typescript
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { spawn, ChildProcess } from 'child_process';
import { EventEmitter } from 'eventemitter3';

export interface SalesforceMCPConfig {
  username: string;
  password: string;
  securityToken: string;
  instanceUrl: string;
  connectTimeout?: number; // Default: 1500ms
  toolTimeout?: number; // Default: 10000ms
}

export class SalesforceMCPClient extends EventEmitter {
  private client: Client | null = null;
  private transport: StdioClientTransport | null = null;
  private process: ChildProcess | null = null;
  private config: SalesforceMCPConfig;
  private connected: boolean = false;

  constructor(config: SalesforceMCPConfig) {
    super();
    this.config = {
      connectTimeout: 1500,
      toolTimeout: 10000,
      ...config,
    };
  }

  async connect(): Promise<void> {
    const startTime = Date.now();
    
    try {
      // Windows-safe npx detection
      const isWindows = process.platform === 'win32';
      const npxCommand = isWindows ? 'npx.cmd' : 'npx';
      
      // Spawn MCP server process
      const serverProcess = spawn(
        npxCommand,
        ['@tsmztech/mcp-server-salesforce'],
        {
          env: {
            ...process.env,
            SALESFORCE_CONNECTION_TYPE: 'User_Password',
            SALESFORCE_USERNAME: this.config.username,
            SALESFORCE_PASSWORD: this.config.password,
            SALESFORCE_TOKEN: this.config.securityToken,
            SALESFORCE_INSTANCE_URL: this.config.instanceUrl,
          },
          stdio: ['pipe', 'pipe', 'pipe'],
        }
      );

      this.process = serverProcess;

      // Connection timeout
      const timeoutPromise = new Promise<never>((_, reject) =>
        setTimeout(
          () => reject(new Error('MCP connection timeout')),
          this.config.connectTimeout
        )
      );

      // Create transport and client
      this.transport = new StdioClientTransport({
        reader: serverProcess.stdout!,
        writer: serverProcess.stdin!,
      });

      this.client = new Client({
        name: 'open-swe-salesforce-client',
        version: '1.0.0',
      }, {
        capabilities: {},
      });

      // Race between connection and timeout
      await Promise.race([
        this.client.connect(this.transport),
        timeoutPromise,
      ]);

      this.connected = true;
      const elapsed = Date.now() - startTime;
      
      this.emit('connected', { elapsed });
      console.log(`✅ Salesforce MCP connected in ${elapsed}ms`);

    } catch (error) {
      this.connected = false;
      this.emit('error', { error });
      throw error;
    }
  }

  async listTools(): Promise<any[]> {
    if (!this.client || !this.connected) {
      throw new Error('MCP client not connected');
    }

    const response = await this.client.listTools();
    return response.tools || [];
  }

  async callTool(name: string, args: any): Promise<any> {
    if (!this.client || !this.connected) {
      throw new Error('MCP client not connected');
    }

    const startTime = Date.now();
    this.emit('tool.start', { name, args: this.redactArgs(args) });

    try {
      // Tool timeout
      const timeoutPromise = new Promise<never>((_, reject) =>
        setTimeout(
          () => reject(new Error(`Tool ${name} timeout after ${this.config.toolTimeout}ms`)),
          this.config.toolTimeout
        )
      );

      const result = await Promise.race([
        this.client.callTool({ name, arguments: args }),
        timeoutPromise,
      ]);

      const elapsed = Date.now() - startTime;
      this.emit('tool.success', { 
        name, 
        elapsed,
        result: this.redactResult(result),
      });

      return result;
    } catch (error) {
      const elapsed = Date.now() - startTime;
      this.emit('tool.error', { 
        name, 
        elapsed,
        error: error.message,
      });
      throw error;
    }
  }

  async disconnect(): Promise<void> {
    if (this.client) {
      await this.client.close();
      this.client = null;
    }
    if (this.process) {
      this.process.kill();
      this.process = null;
    }
    if (this.transport) {
      await this.transport.close();
      this.transport = null;
    }
    this.connected = false;
    this.emit('disconnected');
  }

  isConnected(): boolean {
    return this.connected;
  }

  private redactArgs(args: any): any {
    // Redact sensitive fields
    const sensitivePatterns = /token|secret|password|cookie|authorization|api[_-]?key/i;
    // Implementation in safety.ts
    return args;
  }

  private redactResult(result: any): any {
    // Redact sensitive fields from results
    return result;
  }
}
```

**3. `tools.ts` (Tool wrapping with timing):**
```typescript
import { StructuredTool } from '@langchain/core/tools';
import { SalesforceMCPClient } from './client.js';
import { redactSensitive, chunkDMLRecords } from './safety.js';
import { z } from 'zod';

export async function wrapSalesforceTools(
  mcpClient: SalesforceMCPClient
): Promise<StructuredTool[]> {
  const rawTools = await mcpClient.listTools();
  
  return rawTools.map((tool) => {
    // Check if it's a DML tool
    const isDMLTool = /^(update|insert|delete|upsert)/i.test(tool.name);
    
    return new StructuredTool({
      name: `salesforce_${tool.name}`,
      description: tool.description || `Salesforce MCP tool: ${tool.name}`,
      schema: z.object(tool.inputSchema?.properties || {}),
      
      func: async (args) => {
        // DML chunking
        if (isDMLTool && args.records && Array.isArray(args.records)) {
          return await executeDMLInChunks(mcpClient, tool.name, args);
        }
        
        // Regular tool call
        const result = await mcpClient.callTool(tool.name, args);
        return JSON.stringify(redactSensitive(result));
      },
    });
  });
}

async function executeDMLInChunks(
  client: SalesforceMCPClient,
  toolName: string,
  args: any
): Promise<string> {
  const chunks = chunkDMLRecords(args.records, 50);
  const results = [];
  
  for (let i = 0; i < chunks.length; i++) {
    const chunkArgs = { ...args, records: chunks[i] };
    
    console.log(`Executing DML chunk ${i + 1}/${chunks.length} (${chunks[i].length} records)`);
    
    try {
      const result = await client.callTool(toolName, chunkArgs);
      results.push({
        chunk: i + 1,
        status: 'success',
        recordCount: chunks[i].length,
        result: redactSensitive(result),
      });
    } catch (error) {
      results.push({
        chunk: i + 1,
        status: 'error',
        recordCount: chunks[i].length,
        error: error.message,
      });
      
      // Stop on error
      console.error(`DML chunk ${i + 1} failed:`, error.message);
      break;
    }
  }
  
  return JSON.stringify({
    totalChunks: chunks.length,
    totalRecords: args.records.length,
    results,
  });
}
```

**4. `safety.ts` (Redaction & Chunking):**
```typescript
export function redactSensitive(data: any): any {
  if (!data) return data;
  
  const sensitivePatterns = [
    /token/i,
    /secret/i,
    /password/i,
    /cookie/i,
    /authorization/i,
    /api[_-]?key/i,
    /session[_-]?id/i,
  ];
  
  const redact = (obj: any): any => {
    if (typeof obj !== 'object' || obj === null) return obj;
    
    if (Array.isArray(obj)) {
      return obj.map(redact);
    }
    
    const result: any = {};
    for (const [key, value] of Object.entries(obj)) {
      if (sensitivePatterns.some(pattern => pattern.test(key))) {
        result[key] = '[REDACTED]';
      } else if (typeof value === 'object') {
        result[key] = redact(value);
      } else {
        result[key] = value;
      }
    }
    return result;
  };
  
  return redact(data);
}

export function chunkDMLRecords(records: any[], chunkSize: number = 50): any[][] {
  const chunks: any[][] = [];
  for (let i = 0; i < records.length; i += chunkSize) {
    chunks.push(records.slice(i, i + chunkSize));
  }
  return chunks;
}
```

**5. `events.ts` (Event Bus):**
```typescript
import { EventEmitter } from 'eventemitter3';

export interface McpServerStatus {
  status: 'connected' | 'disconnected' | 'error';
  toolCount: number;
  lastMessage?: string;
}

export interface ToolCallEvent {
  timestamp: number;
  toolName: string;
  status: 'success' | 'error';
  durationMs: number;
  args?: any; // Redacted
  error?: string;
}

export class McpEventBus extends EventEmitter<{
  'server.connected': (data: { elapsed: number }) => void;
  'server.disconnected': () => void;
  'server.error': (data: { error: any }) => void;
  'tool.start': (data: { name: string; args: any }) => void;
  'tool.success': (data: { name: string; elapsed: number; result: any }) => void;
  'tool.error': (data: { name: string; elapsed: number; error: string }) => void;
}> {
  private static instance: McpEventBus;
  
  static getInstance(): McpEventBus {
    if (!McpEventBus.instance) {
      McpEventBus.instance = new McpEventBus();
    }
    return McpEventBus.instance;
  }
}
```

---

### **Phase 2: Planner Integration**

**File:** `apps/open-swe/src/utils/salesforce-mcp-tools.ts` (NEW)

```typescript
import { SalesforceMCPClient, wrapSalesforceTools } from '@openswe/salesforce-mcp';
import { StructuredToolInterface } from '@langchain/core/tools';
import { createLogger, LogLevel } from './logger.js';

const logger = createLogger(LogLevel.INFO, 'SalesforceMCP');

let salesforceMcpClient: SalesforceMCPClient | null = null;

export async function getSalesforceMcpTools(): Promise<StructuredToolInterface[]> {
  // Check if enabled
  if (process.env.SALESFORCE_MCP_ENABLED !== 'true') {
    logger.info('Salesforce MCP disabled');
    return [];
  }

  try {
    // Initialize client if not already done
    if (!salesforceMcpClient) {
      const config = {
        username: process.env.SALESFORCE_USERNAME!,
        password: process.env.SALESFORCE_PASSWORD!,
        securityToken: process.env.SALESFORCE_TOKEN!,
        instanceUrl: process.env.SALESFORCE_INSTANCE_URL!,
        connectTimeout: parseInt(process.env.SALESFORCE_MCP_CONNECT_TIMEOUT || '1500'),
        toolTimeout: parseInt(process.env.SALESFORCE_MCP_TIMEOUT || '10000'),
      };

      // Validate credentials
      if (!config.username || !config.password || !config.securityToken || !config.instanceUrl) {
        logger.warn('Salesforce MCP credentials missing, skipping');
        return [];
      }

      salesforceMcpClient = new SalesforceMCPClient(config);
      await salesforceMcpClient.connect();
      
      logger.info('Salesforce MCP client connected successfully');
    }

    // Get wrapped tools
    const tools = await wrapSalesforceTools(salesforceMcpClient);
    
    logger.info(`Salesforce MCP tools loaded: ${tools.length} tools`, {
      tools: tools.map(t => t.name),
    });

    return tools;

  } catch (error) {
    logger.error('Failed to initialize Salesforce MCP', {
      error: error instanceof Error ? error.message : String(error),
    });
    
    // Graceful degradation - return empty array
    salesforceMcpClient = null;
    return [];
  }
}

// Cleanup function
export async function disconnectSalesforceMcp(): Promise<void> {
  if (salesforceMcpClient) {
    await salesforceMcpClient.disconnect();
    salesforceMcpClient = null;
  }
}
```

**File:** `apps/open-swe/src/graphs/planner/nodes/generate-message/index.ts`

**Update line ~113:**
```typescript
import { getSalesforceMcpTools } from '../../../../utils/salesforce-mcp-tools.js';

// In generateAction function:
const mcpTools = await getMcpTools(config);
const salesforceMcpTools = await getSalesforceMcpTools(); // NEW!

const tools = [
  createGrepTool(state, config),
  createShellTool(state, config),
  createViewTool(state, config),
  createScratchpadTool("when generating a final plan, after all context gathering is complete"),
  createGetURLContentTool(state),
  createSearchDocumentForTool(state, config),
  ...mcpTools,
  ...salesforceMcpTools, // NEW - Salesforce tools added!
];

logger.info(`MCP tools added to Planner: ${mcpTools.map((t) => t.name).join(", ")}`);
logger.info(`Salesforce MCP tools added to Planner: ${salesforceMcpTools.length} tools`, {
  toolNames: salesforceMcpTools.map(t => t.name),
});
```

---

### **Phase 3: /Admin Command Routing**

**File:** `packages/shared/src/open-swe/types.ts`

**Add Task metadata (line ~79):**
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
  metadata?: {
    adminMode?: boolean;
    originalCommand?: string; // Original /Admin command
  };
};
```

**File:** `apps/open-swe/src/graphs/manager/nodes/classify-message/schemas.ts`

**Add admin route:**
```typescript
export const CLASSIFICATION_SCHEMA = z.object({
  internal_reasoning: z.string(),
  response: z.string(),
  route: z.enum([
    "start_planner",
    "start_planner_for_followup",
    "start_admin_planner", // NEW: Admin mode routing
    "continue_conversation",
    "error",
  ]),
});
```

**File:** `apps/open-swe/src/graphs/manager/nodes/classify-message/index.ts`

**Add /Admin detection (line ~58-66):**
```typescript
export async function classifyMessage(
  state: ManagerGraphState,
  config: GraphConfig,
): Promise<Command> {
  const userMessage = state.messages.findLast(isHumanMessage);
  if (!userMessage) {
    throw new Error("No human message found.");
  }

  // NEW: Detect /Admin command
  const messageText = getMessageContentString(userMessage.content);
  const isAdminCommand = messageText.trim().startsWith('/Admin ');
  
  let processedMessage = userMessage;
  let adminMode = false;
  
  if (isAdminCommand) {
    adminMode = true;
    const strippedCommand = messageText.trim().substring(7); // Remove "/Admin "
    
    logger.info('Admin command detected', {
      original: messageText,
      stripped: strippedCommand,
    });
    
    // Create new message without /Admin prefix
    processedMessage = new HumanMessage({
      content: strippedCommand,
      id: userMessage.id,
      name: userMessage.name,
    });
    
    // Replace the message in state
    const messages = state.messages.slice(0, -1).concat(processedMessage);
    state = { ...state, messages };
  }

  // Continue with existing classification logic...
  // (but pass adminMode flag through)
```

**Update routing to handle admin (line ~196-220):**
```typescript
if (
  toolCallArgs.route === "start_planner" ||
  toolCallArgs.route === "start_planner_for_followup" ||
  toolCallArgs.route === "start_admin_planner" // NEW
) {
  // Set admin mode in task metadata if admin route
  if (toolCallArgs.route === "start_admin_planner" && adminMode) {
    // Update task plan to mark as admin mode
    const updatedTaskPlan = {
      ...state.taskPlan,
      tasks: state.taskPlan.tasks.map((task, idx) => {
        if (idx === state.taskPlan.activeTaskIndex) {
          return {
            ...task,
            metadata: {
              ...task.metadata,
              adminMode: true,
              originalCommand: messageText,
            },
          };
        }
        return task;
      }),
    };
    
    return new Command({
      update: {
        messages: [aiMessage],
        taskPlan: updatedTaskPlan,
      },
      goto: "start-planner",
    });
  }
  
  // Normal planner routing...
}
```

---

### **Phase 4: Skip Programmer in Admin Mode**

**File:** `apps/open-swe/src/graphs/manager/index.ts`

**Add conditional routing:**
```typescript
import { shouldSkipProgrammer } from './utils/admin-mode.js';

// In graph definition:
.addConditionalEdges(
  'wait-for-planner',
  (state: ManagerGraphState) => {
    // Check if admin mode
    const activeTask = state.taskPlan.tasks[state.taskPlan.activeTaskIndex];
    if (activeTask?.metadata?.adminMode === true) {
      logger.info('Admin mode detected, skipping Programmer');
      return END; // Skip Programmer
    }
    
    // Normal flow
    return 'start-programmer';
  }
)
```

**File:** `apps/open-swe/src/graphs/manager/utils/admin-mode.ts` (NEW)

```typescript
import { ManagerGraphState } from '@openswe/shared/open-swe/manager/types';

export function isAdminMode(state: ManagerGraphState): boolean {
  const activeTask = state.taskPlan.tasks[state.taskPlan.activeTaskIndex];
  return activeTask?.metadata?.adminMode === true;
}

export function shouldSkipProgrammer(state: ManagerGraphState): boolean {
  return isAdminMode(state);
}
```

---

### **Phase 5: Admin UI Tab (Conditional Auto-Switch)**

**File:** `apps/web/src/components/v2/thread-view.tsx`

**Update tab state (line 99):**
```typescript
const [selectedTab, setSelectedTab] = useState<"planner" | "programmer" | "admin">(
  "planner",
);

// NEW: Admin mode detection
const [adminMode, setAdminMode] = useState(false);
const [mcpServerStatus, setMcpServerStatus] = useState<'connected' | 'disconnected' | 'error'>('disconnected');
const [toolCallHistory, setToolCallHistory] = useState<ToolCallRecord[]>([]);
```

**Add auto-switch logic (line ~250):**
```typescript
// NEW: Auto-switch to Admin tab when admin mode plan is approved
useEffect(() => {
  if (plannerSession && plannerValues) {
    const activeTask = plannerValues.taskPlan?.tasks?.[plannerValues.taskPlan.activeTaskIndex];
    const isAdmin = activeTask?.metadata?.adminMode === true;
    
    if (isAdmin && plannerValues.taskPlan?.tasks?.[0]?.planRevisions?.[0]?.plans) {
      // Plan is generated, check if approved
      const planApproved = plannerValues.proposedPlan && plannerValues.proposedPlan.length > 0;
      
      if (planApproved && selectedTab !== 'admin') {
        logger.info('Admin mode plan approved, switching to Admin tab');
        setAdminMode(true);
        setSelectedTab('admin'); // AUTO-SWITCH
      }
    }
  }
}, [plannerSession, plannerValues, selectedTab]);
```

**Add Admin tab in JSX (line ~400):**
```tsx
<Tabs value={selectedTab} onValueChange={(v) => setSelectedTab(v as any)}>
  <TabsList className={cn("grid w-full", adminMode ? "grid-cols-3" : "grid-cols-2")}>
    <TabsTrigger value="planner">Planner</TabsTrigger>
    <TabsTrigger value="programmer">Programmer</TabsTrigger>
    {adminMode && (
      <TabsTrigger value="admin">
        <span className="flex items-center gap-2">
          Admin
          <Badge variant={mcpServerStatus === 'connected' ? 'default' : 'secondary'} className="text-xs">
            {mcpServerStatus}
          </Badge>
        </span>
      </TabsTrigger>
    )}
  </TabsList>
  
  <TabsContent value="planner">
    {/* Existing */}
  </TabsContent>
  
  <TabsContent value="programmer">
    {/* Existing */}
  </TabsContent>
  
  {adminMode && (
    <TabsContent value="admin">
      <AdminPanel 
        serverStatus={mcpServerStatus}
        toolCallHistory={toolCallHistory}
      />
    </TabsContent>
  )}
</Tabs>
```

**Create:** `apps/web/src/components/v2/admin-panel.tsx`

```tsx
"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { CheckCircle2, XCircle, Clock } from "lucide-react";

export interface ToolCallRecord {
  timestamp: number;
  toolName: string;
  status: 'success' | 'error';
  durationMs: number;
  args?: any;
  error?: string;
}

interface AdminPanelProps {
  serverStatus: 'connected' | 'disconnected' | 'error';
  toolCallHistory: ToolCallRecord[];
}

export function AdminPanel({ serverStatus, toolCallHistory }: AdminPanelProps) {
  const serverStatusColor = {
    connected: 'bg-green-500',
    disconnected: 'bg-gray-400',
    error: 'bg-red-500',
  }[serverStatus];

  return (
    <div className="space-y-4 p-4">
      {/* Server Status Card */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center justify-between">
            <span>Salesforce MCP Server</span>
            <Badge 
              variant={serverStatus === 'connected' ? 'default' : 'destructive'}
              className="flex items-center gap-1"
            >
              <div className={`w-2 h-2 rounded-full ${serverStatusColor}`} />
              {serverStatus.toUpperCase()}
            </Badge>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-2 text-sm">
            <div>
              <span className="font-medium">Server:</span> @tsmztech/mcp-server-salesforce
            </div>
            <div>
              <span className="font-medium">Instance:</span> {process.env.NEXT_PUBLIC_SALESFORCE_INSTANCE_URL || 'QAS'}
            </div>
            <div>
              <span className="font-medium">Tools Available:</span> {serverStatus === 'connected' ? 'Yes' : 'No'}
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Tool Call History */}
      <Card>
        <CardHeader>
          <CardTitle>Recent Tool Calls</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-3">
            {toolCallHistory.length === 0 ? (
              <p className="text-sm text-muted-foreground text-center py-4">
                No tool calls yet. Execute a Salesforce command to see activity.
              </p>
            ) : (
              toolCallHistory.slice().reverse().map((call, i) => (
                <div 
                  key={i} 
                  className="border-l-4 border-blue-500 pl-4 py-2 hover:bg-muted/50 transition-colors"
                >
                  <div className="flex items-center justify-between mb-1">
                    <span className="font-mono text-sm font-medium">
                      {call.toolName.replace('salesforce_', '')}
                    </span>
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-muted-foreground flex items-center gap-1">
                        <Clock className="w-3 h-3" />
                        {call.durationMs}ms
                      </span>
                      {call.status === 'success' ? (
                        <CheckCircle2 className="w-4 h-4 text-green-500" />
                      ) : (
                        <XCircle className="w-4 h-4 text-red-500" />
                      )}
                    </div>
                  </div>
                  
                  <div className="text-xs text-muted-foreground">
                    {new Date(call.timestamp).toLocaleString()}
                  </div>
                  
                  {call.args && (
                    <div className="mt-2 text-xs bg-muted p-2 rounded font-mono">
                      {JSON.stringify(call.args, null, 2).substring(0, 200)}
                      {JSON.stringify(call.args).length > 200 && '...'}
                    </div>
                  )}
                  
                  {call.error && (
                    <div className="mt-2 text-xs text-red-500 bg-red-50 dark:bg-red-950 p-2 rounded">
                      {call.error.substring(0, 300)}
                      {call.error.length > 300 && '...'}
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

### **Phase 6: Environment Configuration**

**File:** `apps/open-swe/.env`

**Add Salesforce MCP section:**
```bash
# ===================================
# Salesforce MCP Configuration (QAS)
# ===================================
SALESFORCE_MCP_ENABLED=true
SALESFORCE_INSTANCE_URL=https://test.salesforce.com
SALESFORCE_USERNAME=idant@nvidia.com.qas
SALESFORCE_PASSWORD=EdenDor22222
SALESFORCE_TOKEN=CUIRatIS395Dk7Wp6MQjixp2

# MCP Server Configuration
SALESFORCE_MCP_SERVER=@tsmztech/mcp-server-salesforce
SALESFORCE_MCP_CONNECT_TIMEOUT=1500
SALESFORCE_MCP_TIMEOUT=10000
SALESFORCE_MCP_DML_CHUNK_SIZE=50

# Safety Settings
SALESFORCE_MCP_ALLOW_DML=true
SALESFORCE_MCP_DML_APPROVAL=false  # Set to true for approval workflow later
```

---

## 🛡️ **Safety Mechanisms**

### **1. Connection Safety:**
```typescript
✅ 1.5s connection timeout (fast fail)
✅ Graceful degradation (empty tools array on failure)
✅ Process cleanup on disconnect
✅ Windows-safe npx.cmd detection
```

### **2. Tool Execution Safety:**
```typescript
✅ 10s per tool timeout
✅ Per-record error handling in DML
✅ 50-record chunk limit
✅ All errors logged but not thrown (continue on error)
```

### **3. Data Safety:**
```typescript
✅ Auto-redact sensitive fields (token, password, secret, etc.)
✅ QAS environment only (sandbox)
✅ DML operations logged with full details
✅ Chunk-level error reporting
```

### **4. UI Safety:**
```typescript
✅ Admin tab only shows if adminMode=true
✅ Auto-switch is conditional (feature flag possible)
✅ Existing tabs unaffected
✅ Fallback to Planner if Admin errors
```

---

## ⚠️ **What Could Break & Mitigations**

| What Could Break | Probability | Impact | Mitigation |
|------------------|-------------|--------|------------|
| npx.cmd not found | LOW | MCP won't connect | Check platform, fallback gracefully |
| MCP server not installed | LOW | Tools unavailable | Return empty array, log warning |
| stdio communication hangs | LOW | Timeout | 1.5s connect, 10s tool timeout |
| /Admin misdetected | VERY LOW | Wrong routing | Exact prefix `/Admin ` with space |
| Programmer skipped wrongly | VERY LOW | Tasks incomplete | Only if adminMode === true |
| DML affects wrong records | LOW | Data issue | QAS only, chunking, logging |
| Admin tab breaks UI | VERY LOW | UI issue | Conditional rendering, feature flag |
| Route schema mismatch | LOW | TypeScript error | Add to enum, test builds |

**Overall Risk:** 🟡 **MEDIUM-LOW** with proper implementation

---

## 📋 **Implementation Checklist**

### **Phase 1: Package Setup**
- [ ] Create `packages/salesforce-mcp/` structure
- [ ] Install `@modelcontextprotocol/sdk`
- [ ] Implement `client.ts` with Windows support
- [ ] Implement `tools.ts` with wrapping
- [ ] Implement `safety.ts` with redaction
- [ ] Implement `events.ts` event bus
- [ ] Add to workspace in root `package.json`
- [ ] Test standalone connection

### **Phase 2: Planner Integration**
- [ ] Create `salesforce-mcp-tools.ts` utility
- [ ] Wire into `generate-message/index.ts`
- [ ] Add Salesforce tools to tool array
- [ ] Test Planner can see tools
- [ ] Verify graceful degradation

### **Phase 3: Routing**
- [ ] Add `metadata` to Task type
- [ ] Add `start_admin_planner` route
- [ ] Implement /Admin detection
- [ ] Strip prefix from message
- [ ] Set adminMode flag
- [ ] Test routing logic

### **Phase 4: Skip Programmer**
- [ ] Add `isAdminMode()` utility
- [ ] Update manager graph edges
- [ ] Test Programmer is skipped
- [ ] Test normal flow still works

### **Phase 5: Admin UI**
- [ ] Create `admin-panel.tsx`
- [ ] Add ToolCallRecord type
- [ ] Update thread-view tabs
- [ ] Add admin tab trigger
- [ ] Implement auto-switch
- [ ] Wire event bus to UI state

### **Phase 6: Testing**
- [ ] `/Admin SELECT Name FROM Account LIMIT 3`
- [ ] `/Admin show my open cases`
- [ ] `/Admin close my P2 cases older than 30 days` (DML)
- [ ] Verify normal tasks work
- [ ] Verify Programmer not invoked for /Admin

---

## 🚀 **Ready to Implement?**

**I recommend this sequence:**

1. **Phase 1 (Day 1 Morning):** Create Salesforce MCP package
2. **Phase 2 (Day 1 Afternoon):** Wire into Planner
3. **Phase 3 (Day 2 Morning):** Add /Admin routing
4. **Phase 4 (Day 2 Afternoon):** Skip Programmer logic
5. **Phase 5 (Day 3):** Admin UI tab
6. **Phase 6 (Day 3-4):** Testing & refinement

**Estimated Time:** 3-4 days for full implementation with DML

---

**Shall I proceed with Phase 1: Creating the Salesforce MCP package?** 🚀

I have all the credentials and understand the requirements. Just give me the green light and I'll start building!

