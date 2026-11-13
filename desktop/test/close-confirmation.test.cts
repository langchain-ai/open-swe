const test = require("node:test");
const assert = require("node:assert/strict");
const { CloseConfirmation } = require("../build/close-confirmation.cjs");

test("quit confirmation cancels or quits without duplicate prompts", async () => {
  let respond;
  const prompts = [];
  let quitCount = 0;
  const confirmation = new CloseConfirmation(
    { quit: () => quitCount++ },
    {
      showMessageBox: (options) => {
        prompts.push(options);
        return new Promise((resolve) => {
          respond = resolve;
        });
      },
    },
    "Robin",
  );
  const canceled = confirmation.request("quit", null);
  await confirmation.request("quit", null);
  assert.equal(prompts.length, 1);
  assert.deepEqual(prompts[0].buttons, ["Cancel", "Quit"]);
  assert.equal(prompts[0].defaultId, 0);
  assert.equal(prompts[0].cancelId, 0);
  assert.match(prompts[0].detail, /disconnects.*This Mac threads/);
  respond({ response: 0 });
  await canceled;
  assert.equal(quitCount, 0);
  const accepted = confirmation.request("quit", null);
  respond({ response: 1 });
  await accepted;
  assert.equal(quitCount, 1);
});

test("window confirmation cancels or closes the selected window", async () => {
  let respond;
  let promptedWindow;
  let options;
  let quitCount = 0;
  const window = {
    closed: false,
    isDestroyed() {
      return this.closed;
    },
    close() {
      this.closed = true;
    },
  };
  const confirmation = new CloseConfirmation(
    { quit: () => quitCount++ },
    {
      showMessageBox: (parent, dialogOptions) => {
        promptedWindow = parent;
        options = dialogOptions;
        return new Promise((resolve) => {
          respond = resolve;
        });
      },
    },
    "Robin",
  );
  const canceled = confirmation.request("close", window);
  assert.equal(promptedWindow, window);
  assert.deepEqual(options.buttons, ["Cancel", "Close Window"]);
  assert.equal(options.defaultId, 0);
  assert.equal(options.cancelId, 0);
  respond({ response: 0 });
  await canceled;
  assert.equal(window.closed, false);
  const accepted = confirmation.request("close", window);
  respond({ response: 1 });
  await accepted;
  assert.equal(window.closed, true);
  assert.equal(quitCount, 0);
});
