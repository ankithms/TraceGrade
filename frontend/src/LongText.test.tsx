// @vitest-environment jsdom
import { afterEach, expect, it } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LongText, textSections } from "./LongText";
import { RecordedData } from "./RecordedData";

afterEach(cleanup);

const prompt = `You are a code reviewer.\n\nIMPORTANT RULES:\n- Report actionable bugs.\n- Be precise.\n\n<repository_context>\nRepository: example/project\nLanguage: Python\n</repository_context>\n\n<annotated_diff>\nFILE: src/one.py\n[line_ref=L1 | ADDED] return total / count\nFILE: src/two.py\n[line_ref=L2 | ADDED] raise ValueError("invalid")\n</annotated_diff>`;

it("finds prompt sections and diff files without discarding any captured text", () => {
  const sections = textSections(prompt);
  expect(sections.map((section) => section.title)).toEqual([
    "Text",
    "IMPORTANT RULES",
    "Repository context",
    "src/one.py",
    "src/two.py",
  ]);
  expect(sections.map((section) => section.text).join("")).toBe(prompt);
  expect(sections.find((section) => section.title === "src/one.py")?.code).toBe(
    true,
  );
  const fenced =
    "Intro\n\n```python\n# Not a heading\n<fake_tag>\n```\n\n## Real heading\nContent";
  expect(textSections(fenced).map((section) => section.title)).toEqual([
    "Text",
    "Real heading",
  ]);
  expect(textSections("plain text")[0].text).toBe("plain text");
});

it("starts compact, expands individual sections, and supports expanding and collapsing all", async () => {
  const user = userEvent.setup();
  render(<LongText text={prompt} />);
  expect(screen.queryAllByRole("region")).toHaveLength(0);
  await user.click(screen.getByRole("button", { name: /IMPORTANT RULES/ }));
  expect(
    screen.getByRole("region", { name: "IMPORTANT RULES content" }).textContent,
  ).toContain("Be precise");
  expect(
    screen.queryByRole("region", { name: "src/one.py content" }),
  ).toBeNull();
  await user.click(screen.getByRole("button", { name: "All 5" }));
  await user.click(screen.getByRole("button", { name: "Expand all" }));
  expect(screen.getAllByRole("region")).toHaveLength(5);
  await user.click(screen.getByRole("button", { name: "Collapse all" }));
  expect(screen.queryAllByRole("region")).toHaveLength(0);
});

it("searches collapsed content, highlights literal matches and restores the outline on clear", async () => {
  const user = userEvent.setup();
  const { container } = render(<LongText text={prompt} />);
  await user.type(
    screen.getByRole("searchbox", { name: "Search this text" }),
    "ValueError(",
  );
  expect(screen.getByRole("status").textContent).toContain("1 match");
  expect(screen.queryByRole("button", { name: /IMPORTANT RULES/ })).toBeNull();
  expect(
    screen.getByRole("region", { name: "src/two.py content" }).textContent,
  ).toContain('raise ValueError("invalid")');
  expect(container.querySelector("mark")?.textContent).toBe("ValueError(");
  await user.click(screen.getByRole("button", { name: "Clear search" }));
  expect(screen.getByRole("button", { name: /IMPORTANT RULES/ })).toBeTruthy();
  expect(screen.queryAllByRole("region")).toHaveLength(0);
  await user.type(screen.getByRole("searchbox"), "not-present");
  expect(screen.getByText(/No matches in the captured text/)).toBeTruthy();
});

it("lets readers jump directly to context or diffs without scrolling past instructions", async () => {
  const user = userEvent.setup();
  render(<LongText text={prompt} />);
  expect(screen.getByRole("button", { name: /IMPORTANT RULES/ })).toBeTruthy();
  expect(
    screen.queryByRole("button", { name: /Repository context/ }),
  ).toBeNull();
  await user.click(screen.getByRole("button", { name: "Context 1" }));
  expect(
    screen.getByRole("button", { name: /Repository context/ }),
  ).toBeTruthy();
  expect(screen.queryByRole("button", { name: /IMPORTANT RULES/ })).toBeNull();
  await user.click(screen.getByRole("button", { name: "Code & diffs 2" }));
  await user.click(screen.getByRole("button", { name: /src\/one.py/ }));
  expect(
    screen.getByRole("region", { name: "src/one.py content" }).textContent,
  ).toContain("return total / count");
  await user.type(screen.getByRole("searchbox"), "actionable");
  expect(
    screen.getByRole("region", { name: "IMPORTANT RULES content" }),
  ).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Clear search" }));
  expect(
    screen.getByRole("region", { name: "src/one.py content" }),
  ).toBeTruthy();
});

it("shows separated matching passages with nearby lines rather than an entire long section", async () => {
  const user = userEvent.setup();
  const lines = Array.from({ length: 40 }, (_, i) =>
    i === 5 || i === 30 ? `needle ${i}` : `line ${i}`,
  );
  render(<LongText text={lines.join("\n")} />);
  await user.type(screen.getByRole("searchbox"), "NEEDLE");
  const region = screen.getByRole("region");
  expect(within(region).getByText("Lines 4–8")).toBeTruthy();
  expect(within(region).getByText("Lines 29–33")).toBeTruthy();
  expect(region.textContent).not.toContain("line 20");
  expect(screen.getByRole("status").textContent).toContain("2 matches");
});

it("copies all text even while search shows only a matching passage", async () => {
  const user = userEvent.setup();
  render(<LongText text={prompt} />);
  await user.type(screen.getByRole("searchbox"), "Python");
  await user.click(screen.getByRole("button", { name: "Copy text" }));
  expect(await navigator.clipboard.readText()).toBe(prompt);
  expect(screen.getByRole("button", { name: "Copied" })).toBeTruthy();
});

it("uses the section browser for large payloads and retains exact JSON and the truncation notice", async () => {
  const user = userEvent.setup();
  const value = {
    preview: JSON.stringify(prompt.repeat(4)).slice(0, -1),
    truncated: true,
  };
  const { container } = render(<RecordedData value={value} />);
  expect(screen.getByText("Partial capture.")).toBeTruthy();
  expect(screen.getByRole("searchbox")).toBeTruthy();
  expect(screen.queryAllByRole("region")).toHaveLength(0);
  await user.click(screen.getByRole("button", { name: "View JSON" }));
  expect(container.querySelector("pre")?.textContent).toBe(
    JSON.stringify(value, null, 2),
  );
});
