// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";
import { duration, spanTree } from "./api";
import type { Span, TraceDetail, TracePage } from "./api";

const trace: TraceDetail = {
  id: "trace-one",
  project_id: "project-one",
  external_id: "external-one",
  name: "Real code review",
  status: "ok",
  started_at: "2026-10-10T00:00:00Z",
  ended_at: "2026-10-10T00:00:20Z",
  created_at: "2026-10-10T00:00:00Z",
  attributes: { repository: "owner/repo" },
  spans: [],
};
const pipeline: Span = {
  ...trace,
  id: "pipeline",
  trace_id: trace.id,
  parent_span_id: null,
  kind: "operation",
  name: "review pipeline",
  input: null,
  output: null,
  model: null,
  prompt_tokens: null,
  completion_tokens: null,
  total_tokens: null,
  latency_ms: 20000,
  estimated_cost_usd: null,
  error_message: null,
};
const model: Span = {
  ...pipeline,
  id: "model",
  parent_span_id: "pipeline",
  name: "model review",
  kind: "llm",
  model: "gemini-2.5-flash",
  input: "Review this diff",
  output: { summary: "Looks good" },
  prompt_tokens: 12,
  completion_tokens: 3,
  total_tokens: 15,
};
const failed: Span = {
  ...pipeline,
  id: "publish",
  parent_span_id: "pipeline",
  name: "publish review",
  status: "error",
  error_message: "Publishing unavailable",
};
const detail: TraceDetail = { ...trace, spans: [pipeline, model, failed] };
const firstPage: TracePage = { items: [trace], total: 1 };
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  fetchMock = vi.fn(
    async (url: string) =>
      new Response(
        JSON.stringify(url.includes("/traces/trace-one") ? detail : firstPage),
        { status: 200 },
      ),
  );
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
async function connect(
  user: ReturnType<typeof userEvent.setup>,
  name = "Reviewer",
  key = "project-key",
) {
  await user.click(
    screen.queryByRole("button", { name: "+ Add project" }) ??
      screen.getByRole("button", { name: "Connect project" }),
  );
  await user.type(screen.getByLabelText("Project label"), name);
  await user.type(screen.getByLabelText("Project API key"), key);
  await user.click(
    within(screen.getByRole("dialog")).getByRole("button", {
      name: "Connect project",
    }),
  );
  if (!screen.queryByRole("alert"))
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
}

describe("trace browser", () => {
  it("connects, inspects real-shaped model data and exposes handled span failures", async () => {
    const user = userEvent.setup();
    render(<App />);
    await connect(user);
    await user.click(
      await screen.findByRole("button", { name: "Real code review" }),
    );
    expect((await screen.findAllByText("15")).length).toBe(2);
    await user.click(screen.getByRole("tab", { name: "Output" }));
    expect(screen.getByRole("tabpanel").textContent).toContain("Looks good");
    await user.click(screen.getByRole("tab", { name: "Input" }));
    expect(screen.getByRole("tabpanel").textContent).toContain(
      "Review this diff",
    );
    await user.click(
      screen.getByRole("button", { name: /publish review Operation/ }),
    );
    expect(screen.getByText("Publishing unavailable")).toBeTruthy();
    expect(
      fetchMock.mock.calls.every(
        (call: unknown[]) =>
          (call[1] as RequestInit).headers &&
          ((call[1] as RequestInit).headers as Record<string, string>)
            .Authorization === "Bearer project-key",
      ),
    ).toBe(true);
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });
  it("rejects invalid keys without connecting or echoing credentials", async () => {
    fetchMock.mockResolvedValue(new Response("{}", { status: 401 }));
    const user = userEvent.setup();
    render(<App />);
    await connect(user, "Invalid", "secret-key");
    expect((await screen.findByRole("alert")).textContent).toContain(
      "invalid or revoked",
    );
    expect(screen.getByRole("dialog").textContent).not.toContain("secret-key");
    expect(screen.queryByLabelText("Select project")).toBeNull();
  });
  it("isolates projects, clears old detail and supports an empty project", async () => {
    fetchMock.mockImplementation(
      async (_url: string, options: RequestInit) =>
        new Response(
          JSON.stringify(
            (options.headers as Record<string, string>).Authorization ===
              "Bearer empty-key"
              ? { items: [], total: 0 }
              : firstPage,
          ),
        ),
    );
    const user = userEvent.setup();
    render(<App />);
    await connect(user);
    await screen.findByRole("button", { name: "Real code review" });
    await connect(user, "Empty project", "empty-key");
    expect(
      await screen.findByText("Your first trace starts here."),
    ).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: "Real code review" }),
    ).toBeNull();
    const project = screen.getByLabelText(
      "Select project",
    ) as HTMLSelectElement;
    await user.selectOptions(
      project,
      within(project).getByRole("option", { name: "Reviewer" }),
    );
    expect(
      await screen.findByRole("button", { name: "Real code review" }),
    ).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Disconnect" }));
    expect(
      await screen.findByText("Your first trace starts here."),
    ).toBeTruthy();
  });
  it("paginates through the API and filters the loaded page", async () => {
    fetchMock.mockImplementation(
      async (url: string) =>
        new Response(
          JSON.stringify({
            items: [
              {
                ...trace,
                name: url.includes("offset=25")
                  ? "Second page review"
                  : trace.name,
              },
            ],
            total: 30,
          }),
        ),
    );
    const user = userEvent.setup();
    render(<App />);
    await connect(user);
    await user.click(await screen.findByRole("button", { name: "Next" }));
    expect(
      await screen.findByRole("button", { name: "Second page review" }),
    ).toBeTruthy();
    await user.type(
      screen.getByLabelText("Search traces on this page"),
      "absent",
    );
    expect(screen.getByText(/No matching traces on this page/)).toBeTruthy();
    expect(screen.getByText("26–30 of 30 traces")).toBeTruthy();
  });
  it("shows detail errors and retries without leaking the previous selection", async () => {
    const user = userEvent.setup();
    render(<App />);
    await connect(user);
    const button = await screen.findByRole("button", {
      name: "Real code review",
    });
    fetchMock.mockImplementation(
      async (url: string) =>
        new Response(
          JSON.stringify(url.includes("/traces/") ? {} : firstPage),
          { status: url.includes("/traces/") ? 404 : 200 },
        ),
    );
    await user.click(button);
    expect((await screen.findByRole("alert")).textContent).toContain(
      "Trace not found in this project",
    );
    fetchMock.mockResolvedValue(new Response(JSON.stringify(detail)));
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect((await screen.findAllByText("15")).length).toBe(2);
  });
  it("keeps connection keyboard focus inside the dialog and closes on Escape", async () => {
    const user = userEvent.setup();
    render(<App />);
    const opener = screen.getByRole("button", { name: "Connect project" });
    await user.click(opener);
    expect(document.activeElement).toBe(screen.getByLabelText("Project label"));
    const close = screen.getByRole("button", {
      name: "Close connection dialog",
    });
    close.focus();
    await user.tab({ shift: true });
    expect(document.activeElement).toBe(
      screen.getByLabelText("Project API key"),
    );
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(document.activeElement).toBe(opener);
  });
  it("keeps the selected span and tab visible while refreshing, including a failed refresh", async () => {
    const user = userEvent.setup();
    render(<App />);
    await connect(user);
    await user.click(
      await screen.findByRole("button", { name: "Real code review" }),
    );
    await user.click(await screen.findByRole("tab", { name: "Output" }));
    let finish!: (response: Response) => void;
    fetchMock.mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    );
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    expect(screen.getByRole("status").textContent).toContain(
      "Updating trace details",
    );
    expect(screen.getByRole("tabpanel").textContent).toContain("Looks good");
    finish(new Response(JSON.stringify(detail)));
    await waitFor(() => expect(screen.queryByRole("status")).toBeNull());
    expect(
      screen.getByRole("tab", { name: "Output" }).getAttribute("aria-selected"),
    ).toBe("true");
    fetchMock.mockResolvedValueOnce(new Response("{}", { status: 500 }));
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    expect((await screen.findByRole("alert")).textContent).toContain(
      "HTTP 500",
    );
    expect(screen.getByRole("tabpanel").textContent).toContain("Looks good");
  });

  it("uses a single keyboard tab stop and arrow-key navigation for span tabs", async () => {
    const user = userEvent.setup();
    render(<App />);
    await connect(user);
    await user.click(
      await screen.findByRole("button", { name: "Real code review" }),
    );
    const overview = await screen.findByRole("tab", { name: "Overview" });
    overview.focus();
    await user.keyboard("{ArrowRight}");
    const input = screen.getByRole("tab", { name: "Input" });
    expect(document.activeElement).toBe(input);
    expect(input.tabIndex).toBe(0);
    expect(overview.tabIndex).toBe(-1);
    expect(screen.getByRole("tabpanel").textContent).toContain(
      "Review this diff",
    );
    await user.keyboard("{End}");
    expect(document.activeElement).toBe(
      screen.getByRole("tab", { name: "Attributes" }),
    );
    await user.keyboard("{Home}");
    expect(document.activeElement).toBe(overview);
  });
});

it("orders nested spans with missing parents and cycles without losing data", () => {
  const orphan = { ...pipeline, id: "orphan", parent_span_id: "absent" };
  const cycle = { ...pipeline, id: "cycle", parent_span_id: "cycle" };
  const rows = spanTree([model, orphan, pipeline, cycle]);
  expect(rows.map((row) => row.span.id)).toEqual([
    "orphan",
    "pipeline",
    "model",
    "cycle",
  ]);
  expect(rows.find((row) => row.span.id === "model")?.depth).toBe(1);
});
it("distinguishes missing duration and zero duration", () => {
  expect(duration(null)).toBe("—");
  expect(duration(0)).toBe("0 ms");
  expect(duration(20502)).toBe("20.50 s");
});
