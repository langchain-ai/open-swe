/* eslint-disable no-console */
import { spawn } from "child_process";
import * as path from "path";

const REQUIRED_ENV = {
  GITHUB_APP_ID: "test",
  GITHUB_APP_PRIVATE_KEY: "test",
  GITHUB_WEBHOOK_SECRET: "test",
};

/**
 * Checks if the development server starts successfully.
 * This script starts the dev server and monitors the output for 30 seconds
 * to detect any errors that might occur during startup.
 */
function checkDevServer(): Promise<void> {
  return new Promise((resolve, reject) => {
    console.log("Starting development server in apps/agents...");

    const scriptDir = __dirname;
    const targetCwd = path.resolve(scriptDir, "..");

    const serverProcess = spawn("yarn", ["dev"], {
      cwd: targetCwd,
      shell: true,
      stdio: "pipe",
      env: REQUIRED_ENV,
    });

    let errorDetected = false;
    let output = "";
    let serverReady = false;

    serverProcess.stdout.on("data", (data) => {
      const message = data.toString();
      output += message;
      console.log(message);
      const lowerCaseMessage = message.toLowerCase();

      if (
        lowerCaseMessage.includes("ready") ||
        lowerCaseMessage.includes("started") ||
        lowerCaseMessage.includes("server running")
      ) {
        serverReady = true;
        console.log("Server ready message detected.");
      }

      // Check for common error patterns in the output
      if (
        lowerCaseMessage.includes("error") ||
        lowerCaseMessage.includes("exception:") ||
        lowerCaseMessage.includes("failed to compile") ||
        lowerCaseMessage.includes("failed")
      ) {
        // Avoid flagging warnings as errors if they contain the word 'error'
        if (!lowerCaseMessage.includes("warning")) {
          errorDetected = true;
          console.error("Error detected in server output!");
          console.error(output);
        } else {
          console.log(
            "Warning detected, not treating as fatal error:",
            message,
          );
        }
      }
    });

    serverProcess.stderr.on("data", (data) => {
      const message = data.toString();
      output += message;
      console.error("stderr:", message); // Log stderr for debugging
      const lowerCaseMessage = message.toLowerCase();

      // Stderr output often indicates errors, but sometimes includes warnings or debug info
      // Be cautious about immediately flagging all stderr as errors
      if (
        !lowerCaseMessage.includes("warning:") &&
        !lowerCaseMessage.includes("deprecated")
      ) {
        errorDetected = true;
        console.error("Potential error detected in server stderr output!");
        console.error(output);
      }
    });

    serverProcess.on("error", (error) => {
      console.error("Failed to start server process:", error);
      errorDetected = true;
    });

    serverProcess.on("close", (code) => {
      console.log(`Server process exited with code ${code}`);
      // If the process exits prematurely (and not killed by us), it might be an error
      // We will rely on the timeout check primarily, but this can be an indicator
      if (code !== 0 && code !== null && !serverProcess.killed) {
        // Check if exit was non-zero and not initiated by our kill()
        // If it exits early *without* the ready flag set, consider it a failure.
        if (!serverReady) {
          console.error(`Server process exited prematurely with code ${code}.`);
          errorDetected = true;
        }
      }
    });

    // Set timeout to wait for server to stabilize or show errors
    const timeoutDuration = 15000; // 15 seconds
    const timeoutId = setTimeout(() => {
      if (!serverProcess.killed) {
        console.log(
          `Timeout reached (${timeoutDuration / 1000}s). Killing server process.`,
        );
        const killed = serverProcess.kill("SIGTERM");
        if (!killed) {
          console.warn(
            "Failed to kill server process with SIGTERM, attempting SIGKILL.",
          );
          serverProcess.kill("SIGKILL");
        }
      } else {
        console.log("Server process already exited before timeout.");
      }

      if (errorDetected) {
        console.error(
          "Server check failed! Errors were detected during server startup.",
        );
        reject(
          new Error(
            "Errors detected during server startup. Check logs for details.",
          ),
        );
      } else if (!serverReady) {
        console.error(
          "Server check failed! Server did not indicate readiness within the timeout.",
        );
        reject(
          new Error(
            "Server did not indicate successful startup within timeout.",
          ),
        );
      } else {
        console.log(
          "Server check passed! Server started successfully and indicated readiness.",
        );
        resolve();
      }
    }, timeoutDuration);

    // Ensure timeout doesn't keep process alive if promise settles early
    serverProcess.on("exit", () => clearTimeout(timeoutId));
  });
}

checkDevServer()
  .then(() => {
    console.log("✅ Dev server check completed successfully!");
    process.exit(0);
  })
  .catch((error) => {
    console.error(`❌ Dev server check failed: ${error.message}`);
    process.exit(1);
  });
