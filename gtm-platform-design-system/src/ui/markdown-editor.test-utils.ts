import { act } from "@testing-library/react";

import {
  getMarkdownEditor,
  setMarkdownEditorValue as writeMarkdownEditorValue,
} from "./markdown-editor";

function setMarkdownEditorValue(from: HTMLElement, markdown: string): void {
  act(() => {
    writeMarkdownEditorValue(from, markdown);
  });
}

export { getMarkdownEditor, setMarkdownEditorValue };
