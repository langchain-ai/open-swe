<div align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="apps/docs/logo/dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="apps/docs/logo/light.svg">
    <img src="apps/docs/logo/dark.svg" alt="AgentMojo Logo" width="35%">
  </picture>
</div>

<div align="center">
  <h1>AgentMojo - An Open-Source Asynchronous Coding Agent</h1>
</div>

AgentMojo is an open-source cloud-based asynchronous coding agent built with [LangGraph](https://langchain-ai.github.io/langgraphjs/). It autonomously understands codebases, plans solutions, and executes code changes across entire repositories—from initial planning to opening pull requests.

> [!TIP]
> Try out AgentMojo yourself using our [public demo](https://agentmojo.langchain.com)!
>
> **Note: you're required to set your own LLM API keys to use the demo.**

> [!NOTE]
> 📚 See the **AgentMojo documentation [here](https://docs.langchain.com/labs/agentmojo/)**
>
> 💬 Read the **announcement blog post [here](https://blog.langchain.com/introducing-agentmojo-an-open-source-asynchronous-coding-agent/)**
>
> 📺 Watch the **announcement video [here](https://youtu.be/TaYVvXbOs8c)**

# Features

![UI Screenshot](./static/ui-screenshot.png)

- 📝 **Planning**: AgentMojo has a dedicated planning step which allows it to deeply understand complex codebases and nuanced tasks. You're also given the ability to accept, edit, or reject the proposed plan before it's executed.
- 🤝 **Human in the loop**: With AgentMojo, you can send it messages while it's running (both during the planning and execution steps). This allows for giving real time feedback and instructions without having to interrupt the process.
- 🏃 **Parallel Execution**: You can run as many AgentMojo tasks as you want in parallel! Since it runs in a sandbox environment in the cloud, you're not limited by the number of tasks you can run at once.
- 🧑‍💻 **End to end task management**: AgentMojo will automatically create GitHub issues for tasks, and create pull requests which will close the issue when implementation is complete.


## Usage

AgentMojo can be used in multiple ways:

- 🖥️ **From the UI**. You can create, manage and execute AgentMojo tasks from the [web application](https://agentmojo.langchain.com). See the ['From the UI' page](https://docs.langchain.com/labs/agentmojo/usage/ui) in the docs for more information.
- 📝 **From GitHub**. You can start AgentMojo tasks directly from GitHub issues simply by adding a label `agentmojo`, or `agentmojo-auto` (adding `-auto` will cause AgentMojo to automatically accept the plan, requiring no intervention from you). For enhanced performance on complex tasks, use `agentmojo-max` or `agentmojo-max-auto` labels which utilize Claude Opus 4.1 for both planning and programming. See the ['From GitHub' page](https://docs.langchain.com/labs/agentmojo/usage/github) in the docs for more information.

# Documentation

To get started using AgentMojo locally, see the [documentation here](https://docs.langchain.com/labs/agentmojo/).

