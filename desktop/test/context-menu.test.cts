const test = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { runInNewContext } = require("node:vm");
import type { MenuItemConstructorOptions } from "electron";

const source = readFileSync(require.resolve("../build/main.cjs"), "utf8");

test("context menus copy the link destination separately from selected text", () => {
  const copied: string[] = [];
  const menus: MenuItemConstructorOptions[][] = [];
  const window = { webContents: {} };
  const context = {
    window,
    clipboard: { writeText: (text: string) => copied.push(text) },
    Menu: {
      buildFromTemplate: (template: MenuItemConstructorOptions[]) => ({
        popup: (options: { window: object }) => {
          assert.equal(options.window, window);
          menus.push(template);
        },
      }),
    },
    params: {
      linkURL: "https://example.com/full-target?query=1#section",
      selectionText: "Short label",
      isEditable: false,
      editFlags: {
        canCut: true,
        canCopy: true,
        canPaste: true,
        canSelectAll: true,
      },
      misspelledWord: "wrod",
      dictionarySuggestions: ["word"],
    },
  };
  const handler = source.slice(
    source.indexOf("function showEditContextMenu("),
    source.indexOf("function createWindow("),
  );
  for (const selectionText of ["Short label", ""]) {
    context.params.selectionText = selectionText;
    runInNewContext(
      handler + "\nshowEditContextMenu(window, params);",
      context,
    );
    const items = menus.pop();
    assert.ok(items);
    const link = items.find((item) => item.label === "Copy Link Address");
    assert.ok(link?.click);
    Reflect.apply(link.click, undefined, []);
    assert.equal(copied.pop(), context.params.linkURL);
    assert.equal(
      items.some((item) => item.role === "copy"),
      Boolean(selectionText),
    );
  }
  context.params.linkURL = "";
  context.params.selectionText = "Plain selected text";
  runInNewContext(handler + "\nshowEditContextMenu(window, params);", context);
  const items = menus.pop();
  assert.ok(items);
  assert.equal(items.length, 1);
  assert.equal(items[0].role, "copy");
  context.params.selectionText = "";
  runInNewContext(handler + "\nshowEditContextMenu(window, params);", context);
  assert.equal(menus.length, 0);
  context.params.isEditable = true;
  runInNewContext(handler + "\nshowEditContextMenu(window, params);", context);
  const editableItems = menus.pop();
  assert.ok(editableItems);
  assert.deepEqual(
    Array.from(editableItems, (item) => item.role ?? item.label ?? item.type),
    [
      "word",
      "Add to Dictionary",
      "separator",
      "cut",
      "copy",
      "paste",
      "separator",
      "selectAll",
    ],
  );
});
