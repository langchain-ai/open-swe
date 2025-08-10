# Contributing to Open SWE

We ❤️ contributions! Thank you for your interest in improving **Open SWE**.  
This document outlines the process for contributing code, documentation, or ideas.

---

## 🛠 Ways to Contribute

You can help improve Open SWE in many ways:
- **Report bugs** by opening a [GitHub issue](../../issues) with clear reproduction steps.
- **Suggest features** by creating an issue with the `enhancement` label.
- **Improve documentation** by fixing typos, adding examples, or expanding explanations.
- **Submit code changes** via pull requests.

---

## 📋 Before You Start

1. **Check existing issues/PRs** to avoid duplicates.
2. **Discuss major changes** by opening an issue before starting work.
3. **Follow our Code of Conduct** (see [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)).

---

## ⚙️ Development Setup

1. **Fork** this repository.
2. **Clone** your fork:
   ```bash
   git clone https://github.com/<your-username>/open-swe.git
   cd open-swe
3. Install dependencies:

```bash
npm install
```

4. Run in development mode:

```bash
npm run dev
```

## ✅ Pull Request Guidelines

To ensure a smooth review process:

Create a feature branch:

```bash
git checkout -b feat/short-description
```

Keep PRs focused on a single change.

Write clear commit messages:

Example: fix: handle null inputs in plan parser

Run tests before submitting:

```bash
npm test
```

Ensure code follows our style rules (linting & formatting):

```bash
npm run lint
npm run format
```

## 📜 Code Style

We follow:

JavaScript/TypeScript best practices

Prettier formatting

ESLint rules (see .eslintrc)

## 🧪 Testing

All new code must include:

Unit tests for logic

Integration tests for major workflows

Run all tests:

```bash
npm test
```

## 🔒 License & Sign-off

By contributing, you agree that your contributions will be licensed under the same license as the project (MIT).
Please include the Developer Certificate of Origin (DCO) sign-off in your commits:

```bash
git commit -s -m "feat: add new parallel execution mode"
```

## 📬 Contact

For questions or help, reach out via:

GitHub Discussions: [link-to-discussions]

Email: opensource@langchain.com

Thank you for making Open SWE better! 🚀
