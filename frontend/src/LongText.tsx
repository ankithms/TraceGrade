import { useEffect, useId, useMemo, useRef, useState } from "react";

type SectionGroup = "text" | "context" | "code";
export type TextSection = {
  title: string;
  text: string;
  code: boolean;
  group: SectionGroup;
};
const groupLabels = { text: "Text", context: "Context", code: "Code & diffs" };
const count = new Intl.NumberFormat();
const label = (text: string) => {
  const words = text.replace(/[_-]/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
};

// Keep the original text in contiguous slices. Headings are navigation aids,
// not summaries, and code fences must not be mistaken for prompt sections.
export function textSections(text: string, code = false): TextSection[] {
  const starts: {
    offset: number;
    title: string;
    code: boolean;
    group: SectionGroup;
  }[] = [];
  let offset = 0;
  let tag = "";
  let fence = "";
  let blank = true;
  for (const line of text.split(/(?<=\n)/)) {
    const trimmed = line.trim();
    const marker = /^(`{3,}|~{3,})/.exec(trimmed)?.[1];
    if (marker) {
      if (!fence) fence = marker;
      else if (marker[0] === fence[0] && marker.length >= fence.length)
        fence = "";
    } else if (!fence) {
      const opening = /^<([a-zA-Z][\w-]*)>$/.exec(trimmed);
      const closing = /^<\/([a-zA-Z][\w-]*)>$/.exec(trimmed);
      const file = /^(?:FILE: |diff --git a\/.* b\/)(.+)$/.exec(line.trimEnd());
      const heading =
        !tag &&
        !code &&
        (/^#{1,6}\s+(.+?)\s*#*\s*$/.exec(line)?.[1] ??
          (blank
            ? /^([A-Za-z][A-Za-z ,/'()-]{2,85}):\s*$/.exec(line)?.[1]
            : undefined));
      if (opening && !tag) {
        tag = opening[1];
        const isCode = /diff|code|patch/i.test(tag);
        starts.push({
          offset,
          title: label(tag),
          code: isCode,
          group: isCode ? "code" : "context",
        });
      } else if (closing?.[1] === tag) {
        tag = "";
      } else if (file && (!tag || /diff|patch/i.test(tag))) {
        const previous = starts[starts.length - 1];
        if (
          previous?.code &&
          /^\s*<[\w-]+>\s*$/.test(text.slice(previous.offset, offset))
        ) {
          previous.title = file[1].trim();
        } else {
          starts.push({
            offset,
            title: file[1].trim(),
            code: true,
            group: "code",
          });
        }
      } else if (heading) {
        starts.push({ offset, title: heading, code: false, group: "text" });
      }
    }
    blank = !trimmed;
    offset += line.length;
  }
  if (!starts.length || starts[0].offset > 0) {
    starts.unshift({
      offset: 0,
      title: code ? "Code" : "Text",
      code,
      group: code ? "code" : "text",
    });
  }
  return starts.map((start, index) => ({
    title: start.title,
    code: start.code,
    group: start.group,
    text: text.slice(start.offset, starts[index + 1]?.offset ?? text.length),
  }));
}

function occurrences(text: string, query: string) {
  if (!query) return 0;
  let matches = 0;
  let from = 0;
  while ((from = text.indexOf(query, from)) !== -1) {
    matches++;
    from += query.length;
  }
  return matches;
}

function Highlight({ text, query }: { text: string; query: string }) {
  if (!query) return <>{text}</>;
  const lower = text.toLowerCase();
  const parts = [];
  let from = 0;
  let at: number;
  while ((at = lower.indexOf(query, from)) !== -1) {
    parts.push(
      text.slice(from, at),
      <mark key={at}>{text.slice(at, at + query.length)}</mark>,
    );
    from = at + query.length;
  }
  parts.push(text.slice(from));
  return <>{parts}</>;
}

function Passages({ text, query }: { text: string; query: string }) {
  const lines = text.split("\n");
  const visible = new Set<number>();
  lines.forEach((line, index) => {
    if (line.toLowerCase().includes(query)) {
      for (
        let i = Math.max(0, index - 2);
        i <= Math.min(lines.length - 1, index + 2);
        i++
      )
        visible.add(i);
    }
  });
  const ranges: { start: number; end: number }[] = [];
  for (const index of visible) {
    const last = ranges[ranges.length - 1];
    if (last && last.end === index - 1) last.end = index;
    else ranges.push({ start: index, end: index });
  }
  return ranges.map(({ start, end }) => (
    <div className="text-passage" key={start}>
      <div className="text-line-label">
        Lines {count.format(start + 1)}–{count.format(end + 1)}
      </div>
      <Highlight text={lines.slice(start, end + 1).join("\n")} query={query} />
    </div>
  ));
}

export function LongText({
  text,
  code = false,
}: {
  text: string;
  code?: boolean;
}) {
  const id = useId();
  const sections = useMemo(() => textSections(text, code), [text, code]);
  const [query, setQuery] = useState("");
  const [group, setGroup] = useState<SectionGroup | "all">(sections[0].group);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [copyState, setCopyState] = useState("Copy text");
  const timeout = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(timeout.current), []);
  const search = query.trim().toLowerCase();
  const matches = sections.map((section) =>
    occurrences(section.text.toLowerCase(), search),
  );
  const matchCount = matches.reduce((sum, n) => sum + n, 0);
  const groups = (["text", "context", "code"] as const).filter((item) =>
    sections.some((section) => section.group === item),
  );
  const inGroup = (section: TextSection) =>
    group === "all" || section.group === group || groups.length === 1;
  const allOpen = sections.every(
    (section, index) => !inGroup(section) || expanded.has(index),
  );
  return (
    <div className="long-text">
      <div className="text-overview">
        <strong>Captured text</strong>
        <span>
          {count.format(text.length)} characters ·{" "}
          {count.format(text.split("\n").length)} lines · {sections.length}{" "}
          {sections.length === 1 ? "section" : "sections"}
        </span>
      </div>
      {groups.length > 1 && !search && (
        <div className="text-groups" role="group" aria-label="Section category">
          {(["all", ...groups] as const).map((item) => (
            <button
              type="button"
              key={item}
              aria-pressed={group === item}
              onClick={() => setGroup(item)}
            >
              {item === "all" ? "All" : groupLabels[item]}
              <span>
                {
                  sections.filter(
                    (section) => item === "all" || section.group === item,
                  ).length
                }
              </span>
            </button>
          ))}
        </div>
      )}
      <div className="text-search-row">
        <label className="text-search">
          <span>Search all sections</span>
          <input
            type="search"
            value={query}
            placeholder="Find a rule, file, or phrase…"
            aria-label="Search this text"
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        {query && (
          <button
            type="button"
            className="small-button"
            onClick={() => setQuery("")}
          >
            Clear search
          </button>
        )}
      </div>
      <div className="text-actions">
        {search ? (
          <span role="status">
            {count.format(matchCount)} {matchCount === 1 ? "match" : "matches"}{" "}
            · Matching passages with context
          </span>
        ) : (
          <button
            className="small-button"
            type="button"
            onClick={() =>
              setExpanded(
                new Set(
                  sections.flatMap((section, index) =>
                    inGroup(section)
                      ? allOpen
                        ? []
                        : [index]
                      : expanded.has(index)
                        ? [index]
                        : [],
                  ),
                ),
              )
            }
          >
            {allOpen ? "Collapse all" : "Expand all"}
          </button>
        )}
        <button
          className="small-button"
          type="button"
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(text);
              setCopyState("Copied");
            } catch {
              setCopyState("Copy unavailable");
            }
            clearTimeout(timeout.current);
            timeout.current = setTimeout(() => setCopyState("Copy text"), 2000);
          }}
        >
          <span aria-live="polite">{copyState}</span>
        </button>
      </div>
      {search && !matchCount && (
        <p className="text-no-results">
          No matches in the captured text. Try a different word or clear the
          search.
        </p>
      )}
      <div className="text-sections">
        {sections.map((section, index) => {
          if (search && !matches[index]) return null;
          if (!search && !inGroup(section)) return null;
          const open = !!search || expanded.has(index);
          const preview = section.text
            .trim()
            .split("\n")
            .filter(
              (line) =>
                line.trim() &&
                !/^<\/?[\w-]+>$/.test(line.trim()) &&
                line.replace(/[:#]/g, "").trim() !== section.title,
            )
            .join(" ")
            .slice(0, 180);
          return (
            <section className="text-section" key={index}>
              <button
                type="button"
                className="text-section-heading"
                aria-expanded={open}
                aria-controls={`${id}-${index}`}
                disabled={!!search}
                onClick={() =>
                  setExpanded((previous) => {
                    const next = new Set(previous);
                    if (next.has(index)) next.delete(index);
                    else next.add(index);
                    return next;
                  })
                }
              >
                <span className="text-chevron" aria-hidden="true">
                  {open ? "⌄" : "›"}
                </span>
                <span className="text-section-title">{section.title}</span>
                <span className="text-section-count">
                  {search
                    ? `${matches[index]} ${matches[index] === 1 ? "match" : "matches"}`
                    : `${count.format(section.text.trimEnd().split("\n").length)} lines`}
                </span>
              </button>
              {!open && preview && (
                <p className="text-section-preview">{preview}</p>
              )}
              <div id={`${id}-${index}`} hidden={!open}>
                {open && (
                  <div
                    className={`text-section-body data-text${section.code ? " mono" : ""}`}
                    tabIndex={0}
                    role="region"
                    aria-label={`${section.title} content`}
                  >
                    {search ? (
                      <Passages text={section.text} query={search} />
                    ) : (
                      section.text
                    )}
                  </div>
                )}
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
}
