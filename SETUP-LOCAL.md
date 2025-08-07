# Open SWE - Local First Edition

This document provides instructions for setting up and running the local-first version of Open SWE. This version is designed to run entirely on your local machine, without any cloud dependencies.

## Prerequisites

Before you begin, ensure you have the following software installed:

*   **Node.js:** Version 18 or higher.
*   **Yarn:** Version 3.5.1 or higher.
*   **Docker:** (Optional) For running the agent in a containerized environment.
*   **Ollama:** For running the local LLM. You can download it from [https://ollama.ai/](https://ollama.ai/).

## Setup

1.  **Clone the repository:**

    ```bash
    git clone https://github.com/your-username/open-swe.git
    cd open-swe
    ```

2.  **Install dependencies:**

    ```bash
    yarn install
    ```

## Running the Application

To start the application, run the following command from the root of the repository:

```bash
yarn start:local
```

This will start a guided setup process that will prompt you for the following information:

*   **Path to the local repository:** The full path to the local repository you want to work on. The default is `./repos`.
*   **Ollama API URL:** The URL of your local Ollama API. The default is `http://localhost:11434`.

The script will create a `.env` file in the `apps/open-swe` directory with your settings.

## Configuration

The following environment variables are available for configuration in the `apps/open-swe/.env` file:

*   `OLLAMA_API_URL`: The URL of your local Ollama API.
*   `OLLAMA_MODEL`: The name of the Ollama model to use. Defaults to `llama3`.
*   `LOCAL_REPO_PATH`: The path to the local repository to run the agent on.
*   `PORT`: The port to run the Open SWE server on. Defaults to `2024`.

## Usage

Once the application is running, you can use the CLI to interact with the agent. The CLI will guide you through the process of creating and running tasks.
