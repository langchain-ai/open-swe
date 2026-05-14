import { Box, Text, useApp, useInput } from "ink";
import { useState } from "react";
import { themeColor } from "./theme.js";
import { ARROW_RIGHT_THIN } from "./figures.js";
import type { DeploymentConfig } from "@lib/api-types";

export type MenuSelection =
  | "new"
  | "active-runs"
  | "switch-deployment"
  | "quit";

type Item = {
  id: MenuSelection;
  label: string;
  hint: string;
};

const ITEMS: Item[] = [
  { id: "new", label: "New run", hint: "Start a fresh cloud run on a repo/branch." },
  { id: "active-runs", label: "Active runs", hint: "Attach to a currently-running thread." },
  { id: "switch-deployment", label: "Switch deployment", hint: "Log in to a different Open SWE server." },
  { id: "quit", label: "Quit", hint: "Exit the CLI." },
];

type Props = {
  deployment: DeploymentConfig;
  onSelect: (sel: MenuSelection) => void;
};

export const MainMenu = ({ deployment, onSelect }: Props) => {
  const [idx, setIdx] = useState(0);
  const { exit } = useApp();

  const brand = themeColor("brand");
  const subtle = themeColor("subtle");
  const inactive = themeColor("inactive");
  const selectionBg = themeColor("selectionBg");

  useInput((input, key) => {
    if (key.upArrow) {
      setIdx((i) => (i > 0 ? i - 1 : i));
      return;
    }
    if (key.downArrow) {
      setIdx((i) => (i < ITEMS.length - 1 ? i + 1 : i));
      return;
    }
    if (key.return) {
      const sel = ITEMS[idx]!;
      if (sel.id === "quit") exit();
      else onSelect(sel.id);
      return;
    }
    if (input === "q" || input === "Q") {
      exit();
    }
  });

  return (
    <Box flexDirection="column" paddingY={1}>
      <Box
        borderStyle="round"
        borderColor={brand}
        paddingX={2}
        flexDirection="column"
        marginBottom={1}
      >
        <Box>
          <Text color={brand}>{ARROW_RIGHT_THIN}_ </Text>
          <Text bold>Open SWE</Text>
        </Box>
        <Text color={subtle}>
          {deployment.github_login} @ {deployment.backend_url}
        </Text>
        <Text color={subtle}>↑/↓ navigate  ·  Enter select  ·  q quit</Text>
      </Box>

      <Box flexDirection="column">
        {ITEMS.map((item, i) => {
          const isSelected = i === idx;
          return (
            <Box key={item.id} backgroundColor={isSelected ? selectionBg : undefined}>
              <Text color={isSelected ? brand : inactive}>
                {isSelected ? "› " : "  "}
              </Text>
              <Text bold={isSelected}>{item.label}</Text>
              <Text color={subtle}>  {item.hint}</Text>
            </Box>
          );
        })}
      </Box>
    </Box>
  );
};
