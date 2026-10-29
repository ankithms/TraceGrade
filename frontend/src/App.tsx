import { useEffect, useRef, useState } from "react";
import { AnimatePresence } from "motion/react";
import { Icon } from "./Icon";
import { Welcome } from "./Welcome";
import { ConnectionDialog } from "./ConnectionDialog";
import { RecordedData } from "./RecordedData";
import type { FormEvent, ReactNode } from "react";
import { apiUrl, duration, elapsed, request, spanTree } from "./api";
import type { Connection, Span, Trace, TraceDetail, TracePage } from "./api";

const PAGE_SIZE = 25;
const number = new Intl.NumberFormat();
const date = (value: string) =>
  new Date(value).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
function Status({ value }: { value: string }) {
  return (
    <span className={`badge ${value}`}>
      <i aria-hidden="true" />
      {value === "ok" ? "Success" : value === "error" ? "Error" : "Running"}
    </span>
  );
}
function Metric({
  label,
  value,
  hint,
}: {
  label: string;
  value: ReactNode;
  hint?: string;
}) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
      {hint && <small>{hint}</small>}
    </div>
  );
}
function Copy({ value }: { value: string }) {
  const [state, setState] = useState("Copy ID");
  const timeout = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(timeout.current), []);
  return (
    <button
      className="small-button"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
          setState("Copied");
        } catch {
          setState("Select ID to copy");
        }
        clearTimeout(timeout.current);
        timeout.current = setTimeout(() => setState("Copy ID"), 2000);
      }}
    >
      <Icon name={state === "Copied" ? "check" : "copy"} />
      <span aria-live="polite">{state}</span>
    </button>
  );
}

function TraceView({ trace }: { trace: TraceDetail }) {
  const [selected, setSelected] = useState(
    trace.spans.find((s) => s.kind === "llm")?.id ?? trace.spans[0]?.id,
  );
  const [tab, setTab] = useState("Overview");
  const span =
    trace.spans.find((s) => s.id === selected) ??
    trace.spans.find((s) => s.kind === "llm") ??
    trace.spans[0];
  const tree = spanTree(trace.spans);
  const totalMs = Math.max(
    elapsed(trace) ?? Date.now() - Date.parse(trace.started_at),
    1,
  );
  const tokens = trace.spans.reduce((sum, s) => sum + (s.total_tokens ?? 0), 0);
  const knownTokens = trace.spans.some((s) => s.total_tokens !== null);
  const costs = trace.spans.filter((s) => s.estimated_cost_usd !== null);
  const cost = costs.reduce((sum, s) => sum + Number(s.estimated_cost_usd), 0);
  return (
    <>
      <div className="detail-title">
        <div>
          <div className="eyebrow">TRACE DETAILS</div>
          <h1>{trace.name}</h1>
          <div className="id-line">
            <code>{trace.id}</code>
            <Copy value={trace.id} />
          </div>
        </div>
        <Status value={trace.status} />
      </div>
      <div className="metrics detail-metrics">
        <Metric
          label="Duration"
          value={duration(elapsed(trace))}
          hint={date(trace.started_at)}
        />
        <Metric
          label="Spans"
          value={trace.spans.length}
          hint={`${trace.spans.filter((s) => s.status === "error").length} with errors`}
        />
        <Metric
          label="Recorded tokens"
          value={knownTokens ? number.format(tokens) : "—"}
          hint="Sum of reported span usage"
        />
        <Metric
          label="Recorded cost"
          value={
            costs.length
              ? `$${cost.toLocaleString("en-US", { maximumFractionDigits: 10 })}`
              : "—"
          }
          hint={
            costs.length
              ? `${costs.length} ${costs.length === 1 ? "span reports" : "spans report"} cost`
              : "No cost recorded"
          }
        />
      </div>
      <div className="inspection">
        <section className="span-panel" aria-label="Span hierarchy">
          <div className="panel-heading">
            <h2>Execution</h2>
            <span>{tree.length} spans</span>
          </div>
          <div className="timeline-label">
            <span>SPAN HIERARCHY</span>
            <span>DURATION</span>
          </div>
          {!tree.length && (
            <div className="empty-value">
              No spans have been recorded for this trace.
            </div>
          )}
          {tree.map(({ span: s, depth }) => {
            const offset = Math.max(
              0,
              Math.min(
                100,
                ((Date.parse(s.started_at) - Date.parse(trace.started_at)) /
                  totalMs) *
                  100,
              ),
            );
            const width = Math.min(
              100 - offset,
              Math.max(1, ((elapsed(s) ?? 0) / totalMs) * 100),
            );
            return (
              <button
                key={s.id}
                className={`span-row ${span?.id === s.id ? "selected" : ""}`}
                onClick={() => {
                  setSelected(s.id);
                  setTab("Overview");
                }}
                aria-pressed={span?.id === s.id}
              >
                <div
                  className="span-name"
                  style={{ paddingLeft: Math.min(depth, 6) * 16 }}
                >
                  <span className={`span-icon ${s.status}`}>
                    <Icon name={s.kind === "llm" ? "model" : "operation"} />
                  </span>
                  <span>
                    {s.name}
                    <small>
                      {s.kind === "llm" ? (s.model ?? "LLM") : "Operation"}
                    </small>
                  </span>
                </div>
                <span className="span-time">
                  {duration(s.latency_ms ?? elapsed(s))}
                </span>
                <span className="waterfall" aria-hidden="true">
                  <i
                    className={s.status}
                    style={{ marginLeft: `${offset}%`, width: `${width}%` }}
                  />
                </span>
              </button>
            );
          })}
          <p className="timeline-note">
            Select a span to inspect its captured data.
          </p>
        </section>
        <section className="span-detail" aria-label="Selected span">
          {span ? (
            <>
              <div className="panel-heading">
                <div>
                  <span className="eyebrow">
                    {span.kind === "llm" ? "MODEL CALL" : "OPERATION"}
                  </span>
                  <h2>{span.name}</h2>
                </div>
                <Status value={span.status} />
              </div>
              <div
                className="tabs"
                role="tablist"
                aria-label="Span data"
                onKeyDown={(event) => {
                  const tabs = ["Overview", "Input", "Output", "Attributes"];
                  const index = tabs.indexOf(tab);
                  const next =
                    event.key === "ArrowRight"
                      ? tabs[(index + 1) % tabs.length]
                      : event.key === "ArrowLeft"
                        ? tabs[(index + tabs.length - 1) % tabs.length]
                        : event.key === "Home"
                          ? tabs[0]
                          : event.key === "End"
                            ? tabs[tabs.length - 1]
                            : null;
                  if (next) {
                    event.preventDefault();
                    setTab(next);
                    document.getElementById(`tab-${next}`)?.focus();
                  }
                }}
              >
                {["Overview", "Input", "Output", "Attributes"].map((t) => (
                  <button
                    key={t}
                    role="tab"
                    aria-selected={tab === t}
                    tabIndex={tab === t ? 0 : -1}
                    id={`tab-${t}`}
                    aria-controls="span-content"
                    onClick={() => setTab(t)}
                  >
                    {t}
                  </button>
                ))}
              </div>
              <div
                className="span-content"
                id="span-content"
                role="tabpanel"
                aria-labelledby={`tab-${tab}`}
                tabIndex={0}
              >
                {span.error_message && (
                  <div className="error-box">
                    <strong>Captured error</strong>
                    <p>{span.error_message}</p>
                  </div>
                )}
                {tab === "Overview" ? (
                  <>
                    <dl className="properties">
                      <dt>Model</dt>
                      <dd>{span.model ?? "—"}</dd>
                      <dt>Duration</dt>
                      <dd>{duration(span.latency_ms ?? elapsed(span))}</dd>
                      <dt>Started</dt>
                      <dd>{date(span.started_at)}</dd>
                      <dt>Finished</dt>
                      <dd>
                        {span.ended_at ? date(span.ended_at) : "In progress"}
                      </dd>
                      <dt>Input tokens</dt>
                      <dd>
                        {span.prompt_tokens === null
                          ? "—"
                          : number.format(span.prompt_tokens)}
                      </dd>
                      <dt>Output tokens</dt>
                      <dd>
                        {span.completion_tokens === null
                          ? "—"
                          : number.format(span.completion_tokens)}
                      </dd>
                      <dt>Total tokens</dt>
                      <dd>
                        {span.total_tokens === null
                          ? "—"
                          : number.format(span.total_tokens)}
                      </dd>
                      <dt>Estimated cost</dt>
                      <dd>
                        {span.estimated_cost_usd === null
                          ? "—"
                          : `$${span.estimated_cost_usd}`}
                      </dd>
                      <dt>Span ID</dt>
                      <dd className="mono">{span.id}</dd>
                      <dt>Parent span</dt>
                      <dd className="mono">
                        {span.parent_span_id ?? "Root span"}
                      </dd>
                    </dl>
                    <p className="subtle">
                      Missing metrics were not reported by the application.
                    </p>
                  </>
                ) : (
                  <RecordedData
                    key={`${span.id}-${tab}`}
                    attributes={tab === "Attributes"}
                    value={
                      tab === "Input"
                        ? span.input
                        : tab === "Output"
                          ? span.output
                          : span.attributes
                    }
                  />
                )}
              </div>
            </>
          ) : (
            <div className="empty-value">
              Select a span to inspect its details.
            </div>
          )}
        </section>
      </div>
      <details className="trace-attributes">
        <summary>
          Trace attributes{" "}
          <span>Repository, commit, and application metadata</span>
        </summary>
        <RecordedData value={trace.attributes} attributes />
        <dl className="properties">
          <dt>Project ID</dt>
          <dd className="mono">{trace.project_id}</dd>
          <dt>External ID</dt>
          <dd className="mono">{trace.external_id}</dd>
        </dl>
      </details>
    </>
  );
}

function App() {
  const [connections, setConnections] = useState<Connection[]>([]);
  const [activeId, setActiveId] = useState("");
  const active = connections.find((c) => c.id === activeId);
  const [modal, setModal] = useState(false);
  const [name, setName] = useState("");
  const [key, setKey] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [connectError, setConnectError] = useState("");
  const [page, setPage] = useState<TracePage>({ items: [], total: 0 });
  const [offset, setOffset] = useState(0);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [traceId, setTraceId] = useState<string | null>(null);
  const [detail, setDetail] = useState<TraceDetail | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [loadedResource, setLoadedResource] = useState("");
  const resourceKey = active
    ? `${active.id}:${traceId ?? `page:${offset}`}`
    : "";
  const hasCurrentData = Boolean(active) && loadedResource === resourceKey;
  const connectionOpener = useRef<HTMLElement | null>(null);
  const connectController = useRef<AbortController | null>(null);
  useEffect(() => () => connectController.current?.abort(), []);
  useEffect(() => {
    if (!active) {
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError("");
    if (traceId) {
      request<TraceDetail>(
        `/traces/${encodeURIComponent(traceId)}`,
        active.key,
        controller.signal,
      )
        .then((value) => {
          if (!controller.signal.aborted) {
            setDetail(value);
            setLoadedResource(resourceKey);
          }
        })
        .catch((e) => {
          if (!controller.signal.aborted) setError(e.message);
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false);
        });
    } else {
      request<TracePage>(
        `/traces?limit=${PAGE_SIZE}&offset=${offset}`,
        active.key,
        controller.signal,
      )
        .then((value) => {
          if (!controller.signal.aborted) {
            setPage(value);
            setLoadedResource(resourceKey);
          }
        })
        .catch((e) => {
          if (!controller.signal.aborted) setError(e.message);
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false);
        });
    }
    return () => controller.abort();
  }, [active, offset, traceId, refresh, resourceKey]);
  const switchProject = (id: string) => {
    setLoadedResource("");
    setActiveId(id);
    setTraceId(null);
    setDetail(null);
    setPage({ items: [], total: 0 });
    setOffset(0);
    setQuery("");
    setFilter("all");
    setError("");
  };
  async function connect(event: FormEvent) {
    event.preventDefault();
    setConnecting(true);
    setConnectError("");
    const controller = new AbortController();
    connectController.current = controller;
    try {
      const result = await request<TracePage>(
        "/traces?limit=25&offset=0",
        key.trim(),
        controller.signal,
      );
      const id = crypto.randomUUID();
      setConnections((previous) => [
        ...previous,
        {
          id,
          name: name.trim(),
          key: key.trim(),
          projectId: result.items[0]?.project_id ?? null,
        },
      ]);
      switchProject(id);
      setModal(false);
      setKey("");
      setName("");
    } catch (e) {
      if (!controller.signal.aborted)
        setConnectError(e instanceof Error ? e.message : "Could not connect.");
    } finally {
      if (!controller.signal.aborted) setConnecting(false);
    }
  }
  const visible = page.items.filter(
    (t) =>
      (filter === "all" || t.status === filter) &&
      `${t.name} ${t.id} ${JSON.stringify(t.attributes)}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  const completed = page.items.filter((t) => t.status !== "running");
  const durations = page.items
    .map(elapsed)
    .filter((ms): ms is number => ms !== null);
  const openConnection = () => {
    connectionOpener.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    setModal(true);
    setConnectError("");
  };
  return (
    <>
      <div className="app-shell" inert={modal}>
        <a className="skip-link" href="#main-content">
          Skip to content
        </a>
        <header className="topbar">
          <a
            className="brand"
            href="#"
            onClick={(e) => {
              e.preventDefault();
              setTraceId(null);
            }}
          >
            <span className="brand-mark">
              <Icon name="trace" />
            </span>
            <span className="brand-name">TraceGrade</span>
            <span className="version">BETA</span>
          </a>
          <nav aria-label="Main navigation">
            <button
              className="nav-item current"
              title="Traces"
              aria-current="page"
              onClick={() => setTraceId(null)}
            >
              <Icon name="layers" /> <span>Traces</span>
            </button>
          </nav>
          <div className="documentation-link">
            <a
              href={`${apiUrl.replace(/\/api\/v1$/, "")}/docs`}
              target="_blank"
              title="API documentation (opens in a new tab)"
              rel="noreferrer"
            >
              <Icon name="book" />
              <span>API documentation</span>
              <Icon name="external" />
            </a>
          </div>
          <div className="connection-controls">
            {active && (
              <select
                title={
                  active?.projectId
                    ? `Project ${active.projectId}`
                    : "Connected projects"
                }
                aria-label="Select project"
                value={activeId}
                onChange={(e) => switchProject(e.target.value)}
              >
                {connections.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            )}
            <button className="button compact" onClick={openConnection}>
              {active ? "+ Add project" : "Connect project"}
            </button>
          </div>
        </header>
        <div className="workspace">
          <main id="main-content" tabIndex={-1}>
            {!active ? (
              <Welcome onConnect={openConnection} />
            ) : (
              <>
                <div className="toolbar">
                  {traceId ? (
                    <button
                      className="back-button"
                      onClick={() => setTraceId(null)}
                    >
                      <Icon name="back" /> All traces
                    </button>
                  ) : (
                    <div className="page-heading">
                      <h1>Traces</h1>
                      <p>Explore every run, from request to response.</p>
                    </div>
                  )}
                  <div className="toolbar-actions">
                    <button
                      className="button compact"
                      disabled={loading}
                      onClick={() => setRefresh((n) => n + 1)}
                    >
                      <Icon name="refresh" /> Refresh
                    </button>
                    <button
                      className="text-button"
                      onClick={() => {
                        const remaining = connections.filter(
                          (c) => c.id !== activeId,
                        );
                        setConnections(remaining);
                        switchProject(remaining[0]?.id ?? "");
                      }}
                    >
                      Disconnect
                    </button>
                  </div>
                </div>
                {error && (
                  <div className="error-box" role="alert">
                    <strong>
                      Could not load {traceId ? "trace" : "traces"}
                    </strong>
                    <p>{error}</p>
                    {hasCurrentData && <p>Showing the last loaded data.</p>}
                    <button
                      className="button compact"
                      onClick={() => setRefresh((n) => n + 1)}
                    >
                      Retry
                    </button>
                  </div>
                )}
                {loading && hasCurrentData && (
                  <div className="refresh-status" role="status">
                    <span className="spinner" /> Updating{" "}
                    {traceId ? "trace details" : "traces"}…
                  </div>
                )}
                {loading && !hasCurrentData ? (
                  <div className="loading" role="status">
                    <span className="spinner" />
                    Loading {traceId ? "trace details" : "traces"}…
                  </div>
                ) : (
                  hasCurrentData &&
                  (traceId ? (
                    detail && (
                      <TraceView
                        key={`${active.id}-${detail.id}`}
                        trace={detail}
                      />
                    )
                  ) : (
                    <>
                      <div className="metrics">
                        <Metric
                          label="Total traces"
                          value={number.format(page.total)}
                          hint="In this project"
                        />
                        <Metric
                          label="Success rate"
                          value={
                            completed.length
                              ? `${Math.round((page.items.filter((t) => t.status === "ok").length / completed.length) * 100)}%`
                              : "—"
                          }
                          hint="Completed traces on this page"
                        />
                        <Metric
                          label="Average duration"
                          value={duration(
                            durations.length
                              ? durations.reduce((a, b) => a + b, 0) /
                                  durations.length
                              : null,
                          )}
                          hint="Completed traces on this page"
                        />
                        <Metric
                          label="Errors"
                          value={
                            page.items.filter((t) => t.status === "error")
                              .length
                          }
                          hint="On this page"
                        />
                      </div>
                      <section className="trace-table">
                        <div className="table-toolbar">
                          <label className="search">
                            <Icon name="search" />
                            <input
                              aria-label="Search traces on this page"
                              placeholder="Search this page…"
                              value={query}
                              onChange={(e) => setQuery(e.target.value)}
                            />
                          </label>
                          <select
                            aria-label="Filter status on this page"
                            value={filter}
                            onChange={(e) => setFilter(e.target.value)}
                          >
                            <option value="all">All statuses</option>
                            <option value="ok">Success</option>
                            <option value="error">Error</option>
                            <option value="running">Running</option>
                          </select>
                        </div>
                        {!page.total ? (
                          <div className="empty-state">
                            <Icon name="layers" />
                            <h2>Your first trace starts here.</h2>
                            <p>
                              This key is connected. Run your instrumented
                              application, then refresh to see its traces.
                            </p>
                          </div>
                        ) : (
                          <>
                            <div
                              className="table-scroll"
                              role="region"
                              aria-label="Traces, scroll horizontally for more columns"
                              tabIndex={0}
                            >
                              <table>
                                <thead>
                                  <tr>
                                    <th scope="col">Trace name</th>
                                    <th scope="col">Status</th>
                                    <th scope="col">Started · local time</th>
                                    <th scope="col">Duration</th>
                                    <th scope="col">Repository</th>
                                    <th>
                                      <span className="sr-only">Open</span>
                                    </th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {visible.map((t: Trace) => (
                                    <tr key={t.id}>
                                      <td>
                                        <button
                                          className="trace-link"
                                          onClick={() => setTraceId(t.id)}
                                        >
                                          {t.name}
                                        </button>
                                        <code className="trace-short-id">
                                          {t.id.slice(0, 8)}
                                        </code>
                                      </td>
                                      <td>
                                        <Status value={t.status} />
                                      </td>
                                      <td className="date-cell">
                                        {date(t.started_at)}
                                      </td>
                                      <td className="mono">
                                        {duration(elapsed(t))}
                                      </td>
                                      <td className="repository">
                                        {typeof t.attributes.repository ===
                                        "string"
                                          ? t.attributes.repository
                                          : "—"}
                                      </td>
                                      <td>
                                        <button
                                          className="open-trace"
                                          aria-label={`Open ${t.name} ${t.id.slice(0, 8)}`}
                                          onClick={() => setTraceId(t.id)}
                                        >
                                          <Icon name="chevron" />
                                        </button>
                                      </td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>
                            {!visible.length && (
                              <div className="empty-value">
                                No matching traces on this page. Try another
                                search or status.
                              </div>
                            )}
                          </>
                        )}
                        <div className="pagination">
                          <span>
                            {page.total
                              ? `${offset + 1}–${Math.min(offset + PAGE_SIZE, page.total)} of ${number.format(page.total)} traces`
                              : "0 traces"}
                          </span>
                          <div>
                            <button
                              className="button compact"
                              disabled={offset === 0 || loading}
                              onClick={() =>
                                setOffset((n) => Math.max(0, n - PAGE_SIZE))
                              }
                            >
                              <Icon name="back" /> Previous
                            </button>
                            <button
                              className="button compact"
                              disabled={
                                offset + PAGE_SIZE >= page.total || loading
                              }
                              onClick={() => setOffset((n) => n + PAGE_SIZE)}
                            >
                              Next <Icon name="arrow" />
                            </button>
                          </div>
                        </div>
                      </section>
                    </>
                  ))
                )}
              </>
            )}
            <footer>
              TraceGrade{" "}
              <span>Application observability, one trace at a time.</span>
            </footer>
          </main>
        </div>
      </div>
      <AnimatePresence>
        {modal && (
          <ConnectionDialog
            key="connection"
            opener={connectionOpener.current}
            name={name}
            projectKey={key}
            connecting={connecting}
            error={connectError}
            onName={setName}
            onKey={setKey}
            onClose={() => {
              setModal(false);
              setKey("");
            }}
            onSubmit={connect}
          />
        )}
      </AnimatePresence>
    </>
  );
}
export default App;
