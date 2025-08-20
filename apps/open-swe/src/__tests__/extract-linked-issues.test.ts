import { extractLinkedIssues } from "../routes/github/utils.js";

describe("extractLinkedIssues", () => {
  it("should extract issues with 'fixes #number' format", () => {
    const prBody = "This PR fixes #123 and also fixes #456";
    const result = extractLinkedIssues(prBody);
    expect(result).toEqual([123, 456]);
  });

  it("should extract issues with 'fixes: #number' format", () => {
    const prBody = "This PR fixes: #123 and also fixes: #456";
    const result = extractLinkedIssues(prBody);
    expect(result).toEqual([123, 456]);
  });

  it("should extract issues with mixed formats", () => {
    const prBody = "This PR fixes #123 and also fixes: #456";
    const result = extractLinkedIssues(prBody);
    expect(result).toEqual([123, 456]);
  });

  it("should extract issues with 'closes' keyword", () => {
    const prBody = "closes #789 and closes: #101";
    const result = extractLinkedIssues(prBody);
    expect(result).toEqual([789, 101]);
  });

  it("should extract issues with 'resolves' keyword", () => {
    const prBody = "resolves #999 and resolves: #888";
    const result = extractLinkedIssues(prBody);
    expect(result).toEqual([999, 888]);
  });

  it("should extract issues with singular forms", () => {
    const prBody = "fix #111, close #222, resolve #333";
    const result = extractLinkedIssues(prBody);
    expect(result).toEqual([111, 222, 333]);
  });

  it("should extract issues with singular forms and colon", () => {
    const prBody = "fix: #111, close: #222, resolve: #333";
    const result = extractLinkedIssues(prBody);
    expect(result).toEqual([111, 222, 333]);
  });

  it("should handle case insensitive keywords", () => {
    const prBody = "FIXES #123, Closes: #456, ResolveS #789";
    const result = extractLinkedIssues(prBody);
    expect(result).toEqual([123, 456, 789]);
  });

  it("should remove duplicate issue numbers", () => {
    const prBody = "fixes #123, closes #123, resolves: #123";
    const result = extractLinkedIssues(prBody);
    expect(result).toEqual([123]);
  });

  it("should handle multiple spaces and whitespace variations", () => {
    const prBody = "fixes    #123 and closes:    #456";
    const result = extractLinkedIssues(prBody);
    expect(result).toEqual([123, 456]);
  });

  it("should handle colon with no spaces", () => {
    const prBody = "fixes:#123 and closes:#456";
    const result = extractLinkedIssues(prBody);
    expect(result).toEqual([123, 456]);
  });

  it("should handle colon with spaces on both sides", () => {
    const prBody = "fixes : #123 and closes : #456";
    const result = extractLinkedIssues(prBody);
    expect(result).toEqual([123, 456]);
  });

  it("should return empty array when no linked issues found", () => {
    const prBody =
      "This is just a regular PR description with no linked issues";
    const result = extractLinkedIssues(prBody);
    expect(result).toEqual([]);
  });

  it("should ignore partial matches", () => {
    const prBody = "This prefixes #123 but doesn't actually fix it";
    const result = extractLinkedIssues(prBody);
    expect(result).toEqual([]);
  });

  it("should handle multiline PR bodies", () => {
    const prBody = `
      ## Summary
      This PR fixes several issues
      
      fixes: #123
      closes #456
      
      ## Additional Notes
      Also resolves: #789
    `;
    const result = extractLinkedIssues(prBody);
    expect(result).toEqual([123, 456, 789]);
  });

  it("should handle complex PR body with mixed content", () => {
    const prBody = `
      # Bug Fix PR
      
      This PR addresses multiple issues:
      - fixes #100 (memory leak)
      - closes: #200 (UI bug)  
      - resolves #300 (performance issue)
      
      ## Testing
      Tested with issue #400 but doesn't fix it yet.
      
      Fixes: #500
    `;
    const result = extractLinkedIssues(prBody);
    expect(result).toEqual([100, 200, 300, 500]);
  });
});
