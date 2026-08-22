import { AIMessage } from "@langchain/core/messages"
import { fakeModel } from "@langchain/core/testing"

import { createCodingAgentGraph } from "@open-swe/agent"
import { createLocalWorkspaceBackend } from "@open-swe/workspace"

interface GraphConfig {
  configurable?: Record<string, unknown>
}

export async function agent(config: GraphConfig = {}) {
  void config
  const model = fakeModel()
    .respondWithTools([
      { name: "read_file", id: "read", args: { file_path: "/README.md" } },
    ])
    .respondWithTools([
      {
        name: "write_file",
        id: "write",
        args: {
          file_path: "/greeting.ts",
          content:
            'export function greet(name: string): string {\n  return `Hello, ${name}!`\n}\n',
        },
      },
    ])
    .respondWithTools([
      {
        name: "execute",
        id: "execute",
        args: {
          command:
            'node --input-type=module --eval "import { greet } from \'./greeting.ts\'; if (greet(\'TypeScript\') !== \'Hello, TypeScript!\') process.exit(1)"',
        },
      },
    ])
    .respond(new AIMessage("Done. I added and verified the TypeScript greeting helper."))

  return createCodingAgentGraph({ model, backend: createLocalWorkspaceBackend() })
}
