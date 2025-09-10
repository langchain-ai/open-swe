import { beforeEach, describe, expect, test, jest } from "@jest/globals";

const getMock = jest.fn();
const putMock = jest.fn();

await jest.unstable_mockModule("@langchain/langgraph", () => ({
  getStore: () => ({ get: getMock, put: putMock }),
}));

const { writeScratchpad } = await import("../scratchpad.js");

beforeEach(() => {
  getMock.mockReset().mockResolvedValue(null);
  putMock.mockReset();
});

describe("scratchpad tool saved state", () => {
  test("persists notes between invocations", async () => {
    await writeScratchpad({ scratchpad: ["First"] });
    expect(putMock).toHaveBeenLastCalledWith(["scratchpad"], "notes", {
      notes: ["First"],
    });

    getMock.mockResolvedValueOnce({ value: { notes: ["First"] } });
    await writeScratchpad({ scratchpad: ["Second"] });
    expect(putMock).toHaveBeenLastCalledWith(["scratchpad"], "notes", {
      notes: ["First", "Second"],
    });
  });
});
