import { Dispatch, SetStateAction } from "react";
import { Card, CardDescription, CardHeader, CardTitle } from "../ui/card";
import { GitHubSVG } from "../icons/github";

const GENERATE_RULES_PROMPT = `You're given a task to write a collection of rules, context and guidelines on the repository you're provided. Please gather context on the following categories, then write an \`AGENTS.md\` file in the root of the repository.

- **General Rules**: These are general rules any developer/agent should follow when writing code. It should contain rules such as "When creating new XYZ functions, always first search in the XYZ/ directory to see if one exists, and if not, create it and place it in an existing or new file". Additionally, here is where you'd include context about scripts which are commonly executed, such as linter and formatter scripts.
- **Repository Structure**: This section is where you'll include high level context about how the repository is laid out, any highly useful and not overly obvious information about how the directories/files are structured, different apps/packages/services inside the repository, etc. Do not include every last detail about the repository contents, only a high level technical overview on the repository structure.
- **Dependencies and Installation**: This section should include high level context about how to install dependencies, where to install them, package managers, etc. Do not include overly verbose context in this section as most repositories typically have straightforward dependency management. 
- **Testing Instructions**: A general guide on testing in this repository. It should include context such as testing frameworks, roughly what types of modules should be tested, how to run tests, and any other context a developer wouldn't be able to infer by looking at test files on how to write & run tests. Do not include every last detail about testing in this section, only the most used/important context necessary to run tests.
- **Pull request formatting**: Rules and guidelines around how to format pull request titles and descriptions. This should only be populated if you find specific instructions in the repository around how to format pull request titles and descriptions. If the repository does not have specific instructions, leave this section empty. The agent will already generate well thought out titles and descriptions, so unless there are special rules specific to this repository, leave this section empty.

Ensure each category is properly wrapped in opening and closing XML tags. The tags to use are as follows:
<general_rules>
</general_rules>
<repository_structure>
</repository_structure>
<dependencies_and_installation>
</dependencies_and_installation>
<testing_instructions>
</testing_instructions>
<pull_request_formatting>
</pull_request_formatting>

It is incredibly important that you ALWAYS wrap your sections in the opening and closing XML tags. Failure to do so will result in an invalid file and will not be accepted.

The file should not contain any other content besides those tags, and the rules/context/instructions they contain. Ensure your rules are thoughtful, concise and actually useful to a developer who's never contributed to the repository before. You can think of it as a more structured and directed \`CONTRIBUTING.md\` file.

With all of this in mind, please explore the repository and write this single \`AGENTS.md\` file with the rules/context/instructions gathered!`;

const DEV_README_PROMPT = `Please add a new callout to the root readme in the repository:
"Welcome to Open SWE!"
Make it a 'tip' callout`;

function DevReadmePromptQuickAction({
  setQuickActionPrompt,
}: QuickActionsProps) {
  const nodeEnv = process.env.NODE_ENV;
  if (nodeEnv === "production") {
    return null;
  }
  return (
    <Card
      onClick={() => setQuickActionPrompt(DEV_README_PROMPT)}
      className="border-border bg-card hover:bg-muted/30 dark:hover:bg-muted/20 hover:shadow-primary/2 cursor-pointer py-3 transition-all duration-200 hover:shadow-sm"
    >
      <CardHeader className="px-3">
        <CardTitle className="text-foreground text-sm">
          [DEV] Add Welcome Callout
        </CardTitle>
        <CardDescription className="text-muted-foreground text-xs">
          Add a welcome callout to the root readme.
        </CardDescription>
      </CardHeader>
    </Card>
  );
}

function AgentsMarkdownQuickAction({
  setQuickActionPrompt,
}: QuickActionsProps) {
  return (
    <Card
      onClick={() => setQuickActionPrompt(GENERATE_RULES_PROMPT)}
      className="border-border bg-card hover:bg-muted/30 dark:hover:bg-muted/20 hover:shadow-primary/2 cursor-pointer py-3 transition-all duration-200 hover:shadow-sm"
    >
      <CardHeader className="px-3">
        <CardTitle className="text-foreground text-sm">
          Generate Agent Rules
        </CardTitle>
        <CardDescription className="text-muted-foreground text-xs">
          Generate an AGENTS.md file for the repository.
        </CardDescription>
      </CardHeader>
    </Card>
  );
}

function RepositoryTemplateQuickAction(props: {
  owner: string;
  repo: string;
  title: string;
  description: string;
}) {
  const repoUrl = `https://github.com/${props.owner}/${props.repo}`;
  return (
    <a
      href={repoUrl}
      target="_blank"
      rel="noopener noreferrer"
    >
      <Card className="border-border bg-card hover:bg-muted/30 dark:hover:bg-muted/20 hover:shadow-primary/2 cursor-pointer py-3 transition-all duration-200 hover:shadow-sm">
        <CardHeader className="px-3">
          <CardTitle className="text-foreground text-sm">
            {props.title}
            <p className="text-foreground/80 mt-1 flex items-center gap-1 font-mono text-xs">
              <GitHubSVG
                width="12"
                height="12"
                className="flex-shrink-0"
              />
              {props.owner}/{props.repo}
            </p>
          </CardTitle>
          <CardDescription className="text-muted-foreground text-xs">
            {props.description}
          </CardDescription>
        </CardHeader>
      </Card>
    </a>
  );
}

interface QuickActionsProps {
  setQuickActionPrompt: Dispatch<SetStateAction<string>>;
}

export function QuickActions({ setQuickActionPrompt }: QuickActionsProps) {
  return (
    <div>
      <h2 className="text-foreground mb-3 text-base font-semibold">
        Quick Actions
      </h2>
      <div className="grid gap-3 md:grid-cols-3">
        <DevReadmePromptQuickAction
          setQuickActionPrompt={setQuickActionPrompt}
        />
        <AgentsMarkdownQuickAction
          setQuickActionPrompt={setQuickActionPrompt}
        />
        <RepositoryTemplateQuickAction
          owner="bracesproul"
          repo="typescript-template"
          title="Clone a TypeScript Template"
          description="Bootstrap a new TypeScript project with pre-configured build tools, linting, testing setup, etc."
        />
      </div>
    </div>
  );
}
