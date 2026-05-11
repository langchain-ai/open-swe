import "dotenv/config";
import { Client } from "langsmith";
import { evaluate } from "langsmith/evaluation";
import { HumanMessage, AIMessage } from "@langchain/core/messages";
import { createEvalAgent } from "@agent/graph";
import { evalSystemPrompt } from "@agent/prompts";
import { createLLMAsJudge } from "openevals";
import { ChatOpenAI } from "@langchain/openai";
import type { Run } from "langsmith/schemas";
import { examples } from "./dataset.js";
import type { ApiKeys, ModelConfig } from "@types";

async function runCodaAgent(inputs: { task: string }): Promise<string> {
  const apiKeys: ApiKeys = {
    openai: process.env.OPENAI_API_KEY,
    anthropic: process.env.ANTHROPIC_API_KEY,
    google: process.env.GOOGLE_API_KEY,
  };

  if (!apiKeys.anthropic) {
    throw new Error("ANTHROPIC_API_KEY not found in environment");
  }

  const modelConfig: ModelConfig = { name: "claude-opus-4-7", provider: "anthropic", effort: "high" };
  const agent = createEvalAgent(apiKeys, modelConfig, evalSystemPrompt);

  const messages = [new HumanMessage(inputs.task)];

  try {
    const result = await agent.invoke(
      { messages }
    );

    const lastMessage = result.messages[result.messages.length - 1];
    if (lastMessage instanceof AIMessage) {
      return lastMessage.content as string;
    }
    return "No response";
  } catch (error) {
    console.error("Agent error:", error);
    return `Error: ${error instanceof Error ? error.message : String(error)}`;
  }
}

function hasResponse(run: Run): { key: string; score: number } {
  const output = run.outputs?.output || "";
  const score = output.length > 20 && !output.startsWith("Error:") ? 1 : 0;
  return {
    key: "has_response",
    score,
  };
}

const llmJudge = createLLMAsJudge({
  prompt: `You are evaluating the quality of a coding agent's response.

Task: {inputs}
Reference Answer: {reference_outputs}
Agent's Answer: {outputs}

Evaluate the agent's answer based on:
1. Correctness: Does it answer the question correctly?
2. Completeness: Does it provide sufficient detail?
3. Code Quality: If code is provided, is it well-written and follows best practices?
4. Clarity: Is the explanation clear and easy to understand?

Compare the agent's answer to the reference answer. The agent doesn't need to match the reference exactly, but should be of similar or better quality.

Rate the overall quality from 0.0 to 1.0:
- 0.0-0.3: Poor quality, incorrect or unhelpful
- 0.4-0.6: Acceptable quality, generally correct but could be better
- 0.7-0.9: Good quality, correct and helpful
- 1.0: Excellent quality, comprehensive and well-explained`,
  feedbackKey: "quality",
  model: "gpt-5",
  judge: new ChatOpenAI({
    apiKey: process.env.OPENAI_API_KEY,
  }),
  continuous: true,
  useReasoning: true,
});

async function qualityEvaluator(run: Run, example?: any) {
  if (!example?.outputs?.output) {
    return {
      key: "quality",
      score: 0,
      comment: "No reference output available",
    };
  }

  return await llmJudge({
    inputs: run.inputs?.task || "",
    outputs: run.outputs?.output || "",
    reference_outputs: example.outputs.output,
  });
}

async function runEvaluation() {
  console.log("Starting coda agent evaluation with LangSmith...\n");

  const client = new Client();

  const datasetName = "coda-agent-eval";

  try {
    let dataset;
    try {
      dataset = await client.readDataset({ datasetName });
      console.log(`Using existing dataset: ${datasetName}`);
    } catch {
      console.log(`Creating new dataset: ${datasetName}`);
      dataset = await client.createDataset(datasetName, {
        description: "Evaluation dataset for coda coding agent",
      });

      for (const example of examples) {
        await client.createExample({
          inputs: example.inputs,
          outputs: example.outputs,
          dataset_id: dataset.id,
        });
      }
      console.log(`Added ${examples.length} examples with reference outputs\n`);
    }

    console.log("Running evaluation (this may take a few minutes)...\n");
    await evaluate(
      async (inputs: { task: string }) => {
        console.log(`Evaluating task: ${inputs.task}`);
        const output = await runCodaAgent(inputs);
        return { output };
      },
      {
        data: datasetName,
        evaluators: [hasResponse, qualityEvaluator],
        experimentPrefix: "coda-agent",
        maxConcurrency: 1,
      }
    );

    console.log("\nEvaluation complete!");
    console.log("View results in your LangSmith dashboard");
    console.log(`Project: ${process.env.LANGSMITH_PROJECT || "coda"}`);
  } catch (error) {
    console.error("Evaluation failed:", error);
    process.exit(1);
  }
}

runEvaluation().catch((error) => {
  console.error("Error running evaluation:", error);
  process.exit(1);
});