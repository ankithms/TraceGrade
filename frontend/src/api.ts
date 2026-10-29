export type TraceStatus = "running" | "ok" | "error";
export interface Trace {
  id: string;
  project_id: string;
  external_id: string;
  name: string;
  status: TraceStatus;
  started_at: string;
  ended_at: string | null;
  created_at: string;
  attributes: Record<string, unknown>;
}
export interface Span extends Trace {
  trace_id: string;
  parent_span_id: string | null;
  kind: string;
  input: unknown;
  output: unknown;
  model: string | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  latency_ms: number | null;
  estimated_cost_usd: string | null;
  error_message: string | null;
}
export interface TraceDetail extends Trace {
  spans: Span[];
}
export interface TracePage {
  items: Trace[];
  total: number;
}
export interface Connection {
  id: string;
  name: string;
  key: string;
  projectId: string | null;
}
export const apiUrl = (
  import.meta.env.VITE_API_URL ?? "http://localhost:8000/api/v1"
).replace(/\/$/, "");
export async function request<T>(
  path: string,
  key: string,
  signal?: AbortSignal,
): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  if (signal?.aborted) controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  const timeout = setTimeout(abort, 12000);
  try {
    let response: Response;
    try {
      response = await fetch(`${apiUrl}${path}`, {
        headers: { Authorization: `Bearer ${key}` },
        signal: controller.signal,
        credentials: "omit",
        cache: "no-store",
      });
    } catch (error) {
      if (signal?.aborted) throw error;
      throw new Error(
        "Could not reach TraceGrade. Check that the API is running and allows this dashboard’s origin, then retry.",
      );
    }
    if (!response.ok) {
      const messages: Record<number, string> = {
        401: "This project key is invalid or revoked. Reconnect with an active project API key.",
        403: "This key does not have access to this resource.",
        404: "Trace not found in this project. Check the selected project and refresh the list.",
        429: "Too many requests. Wait a moment, then retry.",
      };
      throw new Error(
        messages[response.status] ??
          `The API returned HTTP ${response.status}. Please retry.`,
      );
    }
    return (await response.json()) as T;
  } finally {
    clearTimeout(timeout);
    signal?.removeEventListener("abort", abort);
  }
}

export function elapsed(trace: Trace): number | null {
  return trace.ended_at
    ? Math.max(0, Date.parse(trace.ended_at) - Date.parse(trace.started_at))
    : null;
}
export function duration(ms: number | null): string {
  if (ms === null) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(2)} s`;
  return `${(ms / 60000).toFixed(1)} min`;
}
export function spanTree(spans: Span[]): { span: Span; depth: number }[] {
  const ids = new Set(spans.map((s) => s.id));
  const children = new Map<string | null, Span[]>();
  for (const span of spans) {
    const parent =
      span.parent_span_id && ids.has(span.parent_span_id)
        ? span.parent_span_id
        : null;
    children.set(parent, [...(children.get(parent) ?? []), span]);
  }
  const result: { span: Span; depth: number }[] = [];
  const visited = new Set<string>();
  const visit = (span: Span, depth: number) => {
    if (visited.has(span.id)) return;
    visited.add(span.id);
    result.push({ span, depth });
    for (const child of children.get(span.id) ?? []) visit(child, depth + 1);
  };
  for (const root of children.get(null) ?? []) visit(root, 0);
  // Keep malformed/cyclic data inspectable without looping forever.
  for (const span of spans) if (!visited.has(span.id)) visit(span, 0);
  return result;
}
