#!/usr/bin/env tsx

import { spawn } from "child_process";
import * as fs from "fs/promises";
import { createLogger, LogLevel } from "../src/utils/logger.js";

const logger = createLogger(LogLevel.INFO, "SWEBenchSetup");

/**
 * Setup script for SWE-bench evaluation environment
 * Installs dependencies, verifies Docker, and prepares directories
 */

interface SetupOptions {
  skipPythonInstall?: boolean;
  skipDockerCheck?: boolean;
  downloadDataset?: boolean;
  datasetName?: string;
  createDirs?: boolean;
}

/**
 * Executes a command and returns the result
 */
async function execCommand(
  command: string,
  args: string[],
): Promise<{ success: boolean; output: string; error?: string }> {
  return new Promise((resolve) => {
    const output: string[] = [];
    const errorOutput: string[] = [];

    const process = spawn(command, args, {
      shell: true,
      stdio: ["inherit", "pipe", "pipe"],
    });

    process.stdout?.on("data", (data) => {
      const text = data.toString();
      output.push(text);
      logger.info(text.trim());
    });

    process.stderr?.on("data", (data) => {
      const text = data.toString();
      errorOutput.push(text);
    });

    process.on("close", (code) => {
      if (code === 0) {
        resolve({
          success: true,
          output: output.join(""),
        });
      } else {
        resolve({
          success: false,
          output: output.join(""),
          error: errorOutput.join("") || `Process exited with code ${code}`,
        });
      }
    });

    process.on("error", (error) => {
      resolve({
        success: false,
        output: output.join(""),
        error: error.message,
      });
    });
  });
}

/**
 * Checks if Python is installed and meets version requirements
 */
async function checkPython(): Promise<boolean> {
  logger.info("Checking Python installation...");

  const result = await execCommand("python", ["--version"]);
  if (!result.success) {
    logger.error("Python is not installed or not in PATH");
    return false;
  }

  // Parse version
  const versionMatch = result.output.match(/Python (\d+)\.(\d+)/);
  if (versionMatch) {
    const major = parseInt(versionMatch[1]);
    const minor = parseInt(versionMatch[2]);
    if (major === 3 && minor >= 8) {
      logger.info(`Python ${major}.${minor} found - OK`);
      return true;
    } else {
      logger.error(`Python 3.8+ required, found ${major}.${minor}`);
      return false;
    }
  }

  logger.error("Could not parse Python version");
  return false;
}

/**
 * Checks if pip is installed
 */
async function checkPip(): Promise<boolean> {
  logger.info("Checking pip installation...");

  const result = await execCommand("python", ["-m", "pip", "--version"]);
  if (result.success) {
    logger.info("pip is installed - OK");
    return true;
  }

  logger.error("pip is not installed");
  return false;
}

/**
 * Installs the SWE-bench Python package
 */
async function installSWEBench(): Promise<boolean> {
  logger.info("Installing SWE-bench Python package...");

  // First, upgrade pip
  logger.info("Upgrading pip...");
  const upgradePipResult = await execCommand("python", [
    "-m",
    "pip",
    "install",
    "--upgrade",
    "pip",
  ]);

  if (!upgradePipResult.success) {
    logger.warn("Failed to upgrade pip, continuing anyway");
  }

  // Install swebench
  logger.info("Installing swebench package...");
  const installResult = await execCommand("python", [
    "-m",
    "pip",
    "install",
    "swebench",
  ]);

  if (installResult.success) {
    logger.info("SWE-bench package installed successfully");

    // Verify installation
    const verifyResult = await execCommand("python", [
      "-c",
      "import swebench; print(f'SWE-bench version: {swebench.__version__}')",
    ]);

    if (verifyResult.success) {
      logger.info(verifyResult.output.trim());
      return true;
    }
  }

  logger.error("Failed to install SWE-bench package", {
    error: installResult.error,
  });
  return false;
}

/**
 * Checks Docker installation and resources
 */
async function checkDocker(): Promise<boolean> {
  logger.info("Checking Docker installation...");

  // Check if Docker is installed
  const versionResult = await execCommand("docker", ["--version"]);
  if (!versionResult.success) {
    logger.error("Docker is not installed or not in PATH");
    return false;
  }

  logger.info(versionResult.output.trim());

  // Check if Docker daemon is running
  logger.info("Checking Docker daemon...");
  const infoResult = await execCommand("docker", ["info", "--format", "json"]);

  if (!infoResult.success) {
    logger.error("Docker daemon is not running");
    return false;
  }

  try {
    const dockerInfo = JSON.parse(infoResult.output);

    // Check memory
    const memoryGB = dockerInfo.MemTotal / (1024 * 1024 * 1024);
    logger.info(`Docker memory: ${memoryGB.toFixed(1)} GB`);

    if (memoryGB < 16) {
      logger.warn(
        `Docker has only ${memoryGB.toFixed(1)} GB memory. ` +
          `SWE-bench recommends 16+ GB for optimal performance.`,
      );
    }

    // Check disk space
    const storageDriver = dockerInfo.Driver || "unknown";
    logger.info(`Docker storage driver: ${storageDriver}`);

    // Run hello-world test
    logger.info("Testing Docker with hello-world container...");
    const helloResult = await execCommand("docker", [
      "run",
      "--rm",
      "hello-world",
    ]);

    if (helloResult.success) {
      logger.info("Docker is working correctly");
      return true;
    } else {
      logger.error("Docker test failed", { error: helloResult.error });
      return false;
    }
  } catch (error) {
    logger.error("Failed to parse Docker info", { error });
    return false;
  }
}

/**
 * Downloads the SWE-bench dataset (optional)
 */
async function downloadDataset(datasetName: string): Promise<boolean> {
  logger.info(`Downloading SWE-bench dataset: ${datasetName}...`);

  // Use Python to download via Hugging Face datasets
  const downloadScript = `
import datasets
import json

try:
    # Load the dataset
    dataset = datasets.load_dataset("${datasetName}", split="test")
    print(f"Downloaded {len(dataset)} instances from {datasetName}")
    
    # Save a sample for verification
    sample = dataset[0] if len(dataset) > 0 else {}
    print(f"Sample instance: {sample.get('instance_id', 'N/A')}")
    
    # Cache is automatically managed by Hugging Face
    print("Dataset cached successfully")
except Exception as e:
    print(f"Error downloading dataset: {e}")
    exit(1)
`;

  const result = await execCommand("python", ["-c", downloadScript]);

  if (result.success) {
    logger.info("Dataset downloaded successfully");
    return true;
  } else {
    logger.error("Failed to download dataset", { error: result.error });
    return false;
  }
}

/**
 * Creates necessary directories for SWE-bench evaluation
 */
async function createDirectories(): Promise<boolean> {
  const directories = [
    "./swe-bench-evals",
    "./swe-bench-evals/predictions",
    "./swe-bench-evals/results",
    "./evaluation_results",
    "./logs",
    "./logs/run_evaluation",
    "./logs/build_images",
    "./logs/build_images/base",
    "./logs/build_images/env",
    "./logs/build_images/instances",
  ];

  logger.info("Creating necessary directories...");

  try {
    for (const dir of directories) {
      await fs.mkdir(dir, { recursive: true });
      logger.info(`Created directory: ${dir}`);
    }
    return true;
  } catch (error) {
    logger.error("Failed to create directories", { error });
    return false;
  }
}

/**
 * Main setup function
 */
async function setup(options: SetupOptions = {}): Promise<boolean> {
  const {
    skipPythonInstall = false,
    skipDockerCheck = false,
    downloadDataset: shouldDownloadDataset = false,
    datasetName = "princeton-nlp/SWE-bench_Lite",
    createDirs = true,
  } = options;

  logger.info("Starting SWE-bench setup...");
  logger.info("=".repeat(50));

  const steps: {
    name: string;
    check: () => Promise<boolean>;
    required: boolean;
  }[] = [
    {
      name: "Python Check",
      check: checkPython,
      required: true,
    },
    {
      name: "pip Check",
      check: checkPip,
      required: true,
    },
    {
      name: "SWE-bench Installation",
      check: skipPythonInstall
        ? async () => {
            logger.info(
              "Skipping SWE-bench installation (--skip-python-install)",
            );
            return true;
          }
        : installSWEBench,
      required: !skipPythonInstall,
    },
    {
      name: "Docker Check",
      check: skipDockerCheck
        ? async () => {
            logger.info("Skipping Docker check (--skip-docker-check)");
            return true;
          }
        : checkDocker,
      required: !skipDockerCheck,
    },
    {
      name: "Dataset Download",
      check: shouldDownloadDataset
        ? () => downloadDataset(datasetName)
        : async () => {
            logger.info(
              "Skipping dataset download (use --download-dataset to enable)",
            );
            return true;
          },
      required: false,
    },
    {
      name: "Directory Creation",
      check: createDirs
        ? createDirectories
        : async () => {
            logger.info("Skipping directory creation (--no-create-dirs)");
            return true;
          },
      required: false,
    },
  ];

  let allPassed = true;

  for (const step of steps) {
    logger.info(`\n${step.name}:`);
    logger.info("-".repeat(30));

    const success = await step.check();

    if (!success && step.required) {
      logger.error(`❌ ${step.name} failed (required)`);
      allPassed = false;
      break;
    } else if (!success) {
      logger.warn(`⚠️  ${step.name} failed (optional)`);
    } else {
      logger.info(`✅ ${step.name} completed successfully`);
    }
  }

  logger.info("\n" + "=".repeat(50));

  if (allPassed) {
    logger.info("✅ SWE-bench setup completed successfully!");
    logger.info("\nNext steps:");
    logger.info("1. Set your GITHUB_PAT environment variable");
    logger.info("2. Run: yarn eval:swe-bench");
    return true;
  } else {
    logger.error("❌ SWE-bench setup failed!");
    logger.error("\nPlease fix the errors above and run setup again.");
    return false;
  }
}

// Parse command line arguments
const args = process.argv.slice(2);
const options: SetupOptions = {
  skipPythonInstall: args.includes("--skip-python-install"),
  skipDockerCheck: args.includes("--skip-docker-check"),
  downloadDataset: args.includes("--download-dataset"),
  datasetName:
    args.find((arg) => arg.startsWith("--dataset="))?.split("=")[1] ||
    "princeton-nlp/SWE-bench_Lite",
  createDirs: !args.includes("--no-create-dirs"),
};

if (args.includes("--help") || args.includes("-h")) {
  logger.info(`
SWE-bench Setup Script

Usage: tsx scripts/setup-swe-bench.ts [options]

Options:
  --skip-python-install    Skip installing the swebench Python package
  --skip-docker-check      Skip Docker verification
  --download-dataset       Download the SWE-bench dataset
  --dataset=NAME          Dataset to download (default: princeton-nlp/SWE-bench_Lite)
  --no-create-dirs        Skip creating directories
  --help, -h              Show this help message

Examples:
  # Full setup
  tsx scripts/setup-swe-bench.ts

  # Setup with dataset download
  tsx scripts/setup-swe-bench.ts --download-dataset

  # Setup for full SWE-bench dataset
  tsx scripts/setup-swe-bench.ts --download-dataset --dataset=princeton-nlp/SWE-bench

  # Quick setup (skip optional steps)
  tsx scripts/setup-swe-bench.ts --skip-docker-check --no-create-dirs
`);
  process.exit(0);
}

// Run setup
setup(options).then((success) => {
  process.exit(success ? 0 : 1);
});
