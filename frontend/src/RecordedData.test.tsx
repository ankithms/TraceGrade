// @vitest-environment jsdom
import { afterEach, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RecordedData } from "./RecordedData";

afterEach(cleanup);

it("shows nested content without losing false, zero, null or empty values", async () => {
  const user = userEvent.setup();
  const value = {
    diff: "- return total\n+ return total / count",
    messages: [{ role: "user", content: "Review this\ncarefully" }],
    metadata: { enabled: false, attempts: 0, missing: null },
    list: [],
    object: {},
    text: "",
  };
  const { container } = render(<RecordedData value={value} />);
  expect(container.querySelector("pre")).toBeNull();
  expect(screen.getByText(/return total/).textContent).toBe(value.diff);
  expect(screen.getByText(/Review this/).textContent).toBe(
    value.messages[0].content,
  );
  for (const text of [
    "false",
    "0",
    "No value",
    "Empty list",
    "Empty object",
    "Empty text",
  ]) {
    expect(screen.getByText(text)).toBeTruthy();
  }
  await user.click(screen.getByRole("button", { name: "View JSON" }));
  expect(container.querySelector("pre")?.textContent).toBe(
    JSON.stringify(value, null, 2),
  );
  await user.click(screen.getByRole("button", { name: "View readable" }));
  expect(container.querySelector("pre")).toBeNull();
  expect(screen.getByText("false")).toBeTruthy();
});

it("keeps recorded strings literal and exposes their original JSON encoding", async () => {
  const user = userEvent.setup();
  const value = '<script>alert("example")</script>\n{"summary":"literal text"}';
  const { container } = render(<RecordedData value={value} />);
  expect(container.querySelector(".data-text")?.textContent).toBe(value);
  expect(container.querySelector("script")).toBeNull();
  await user.click(screen.getByRole("button", { name: "View JSON" }));
  expect(container.querySelector("pre")?.textContent).toBe(
    JSON.stringify(value),
  );
});

it("distinguishes missing payloads from empty attributes", () => {
  const { rerender } = render(<RecordedData value={null} />);
  expect(screen.getByText("No value recorded.")).toBeTruthy();
  rerender(<RecordedData value={{}} attributes />);
  expect(screen.getByText("No attributes recorded.")).toBeTruthy();
});

it("decodes escaped truncated previews while retaining the original captured data", async () => {
  const user = userEvent.setup();
  const value = {
    preview:
      '"\\nReview this diff\\nSay \\"hello\\" and check C:\\\\repo\\ncut off\\u12',
    truncated: true,
  };
  const { container } = render(<RecordedData value={value} />);
  expect(screen.getByText(/Review this diff/).textContent).toBe(
    '\nReview this diff\nSay "hello" and check C:\\repo\ncut off\\u12',
  );
  expect(screen.getByText("Partial capture.")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "View JSON" }));
  expect(container.querySelector("pre")?.textContent).toBe(
    JSON.stringify(value, null, 2),
  );
});
