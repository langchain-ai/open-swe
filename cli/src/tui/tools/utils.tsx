const HOME = process.env.HOME ?? "";

export const tildify = (p: string): string => {
  if (HOME && p.startsWith(HOME)) return "~" + p.slice(HOME.length);
  return p;
};

export const getDisplayPath = (p: string): string => {
  if (!p) return "";
  return tildify(p);
};

export const argString = (
  args: Record<string, unknown>,
  key: string,
): string => {
  const value = args[key];
  return typeof value === "string" ? value : "";
};

export const argFilePath = (args: Record<string, unknown>): string => {
  const filePath =
    typeof args.file_path === "string"
      ? args.file_path
      : typeof args.path === "string"
        ? args.path
        : "";
  return filePath;
};
