<div align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="apps/docs/logo/dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="apps/docs/logo/light.svg">
    <img src="apps/docs/logo/dark.svg" alt="Agent Mojo Logo" width="35%">
  </picture>
</div>

<div align="center">
our Sarcastic 
  <h1>Agent Mojo - Your smartass AI ASSISTANT AND sarcasric Asynchronous Coding Agent</h1>
</div>

Agent Mojo is an open-source cloud-based asynchronous coding agent built with [LangGraph](https://langchain-ai.github.io/langgraphjs/). It autonomously understands codebases, plans solutions, and executes code changes across entire repositories—from initial planning to opening pull requests.

> [!TIP]
> Try out Agent Mojo yourself using our [public demo](https://swe.langchain.com)!
>
> **Note: you're required to set your own LLM API keys to use the demo.**

> [!NOTE]
> 📚 See the **Agent Mojo documentation [here](https://docs.langchain.com/labs/swe/)**
>
> 💬 Read the **announcement blog post [here](https://blog.langchain.com/introducing-agent-mojo-an-open-source-asynchronous-coding-agent/)**
>
> 📺 Watch the **announcement video [here](https://youtu.be/TaYVvXbOs8c)**

# Features

![UI Screenshot](./static/ui-screenshot.png)

- 📝 **Planning**: Agent Mojo has a dedicated planning step which allows it to deeply understand complex codebases and nuanced tasks. You're also given the ability to accept, edit, or reject the proposed plan before it's executed.
- 🤝 **Human in the loop**: With Agent Mojo, you can send it messages while it's running (both during the planning and execution steps). This allows for giving real time feedback and instructions without having to interrupt the process.
- 🏃 **Parallel Execution**: You can run as many Agent Mojo tasks as you want in parallel! Since it runs in a sandbox environment in the cloud, you're not limited by the number of tasks you can run at once.
- 🧑‍💻 **End to end task management**: Agent Mojo will automatically create GitHub issues for tasks, and create pull requests which will close the issue when implementation is complete.


## Usage

Agent Mojo can be used in multiple ways:

- 🖥️ **From the UI**. You can create, manage and execute Agent Mojo tasks from the [web application](https://swe.langchain.com). See the ['From the UI' page](https://docs.langchain.com/labs/swe/usage/ui) in the docs for more information.
- 📝 **From GitHub**. You can start Agent Mojo tasks directly from GitHub issues simply by adding a label `agent-mojo`, or `agent-mojo-auto` (adding `-auto` will cause Agent Mojo to automatically accept the plan, requiring no intervention from you). For enhanced performance on complex tasks, use `agent-mojo-max` or `agent-mojo-max-auto` labels which utilize Claude Opus 4.1 for both planning and programming. See the ['From GitHub' page](https://docs.langchain.com/labs/swe/usage/github) in the docs for more information.

# Documentation

To get started using Agent Mojo locally, see the [documentation here](https://docs.langchain.com/labs/swe/).

