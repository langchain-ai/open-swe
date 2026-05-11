import wrapAnsiNpm from "wrap-ansi";

type WrapAnsiOptions = {
  hard?: boolean;
  wordWrap?: boolean;
  trim?: boolean;
};

export const wrapAnsi: (
  input: string,
  columns: number,
  options?: WrapAnsiOptions,
) => string = wrapAnsiNpm;
