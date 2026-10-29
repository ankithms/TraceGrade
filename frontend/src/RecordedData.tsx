import { useState } from "react";
import { LongText } from "./LongText";

function readablePreview(value: unknown): unknown {
  if (typeof value !== "string" || !value.startsWith('"')) return value;
  try {
    const decoded: unknown = JSON.parse(value);
    return typeof decoded === "string" ? decoded : value;
  } catch {
    // A truncated JSON string has no closing quote. Decode complete escape
    // sequences only; leave any incomplete trailing escape visible.
    return value
      .slice(1)
      .replace(
        /\\(?:["\\/bfnrt]|u[\da-fA-F]{4})/g,
        (escape) => JSON.parse(`"${escape}"`) as string,
      );
  }
}

function ReadableValue({ value, field }: { value: unknown; field?: string }) {
  if (value === null || value === undefined) {
    return <span className="data-empty">No value</span>;
  }
  if (Array.isArray(value)) {
    return value.length ? (
      <ol className="data-list">
        {value.map((item, index) => (
          <li key={index}>
            <ReadableValue value={item} />
          </li>
        ))}
      </ol>
    ) : (
      <span className="data-empty">Empty list</span>
    );
  }
  if (typeof value === "object") {
    const truncated = "truncated" in value && value.truncated === true;
    const preview =
      truncated && "preview" in value && typeof value.preview === "string";
    const entries = Object.entries(value).filter(
      ([key]) => !preview || !["preview", "truncated"].includes(key),
    );
    return entries.length || preview ? (
      <>
        {preview && "preview" in value && (
          <>
            <p className="data-truncation">
              <strong>Partial capture.</strong> Only the recorded preview is
              available.
            </p>
            <ReadableValue value={readablePreview(value.preview)} />
          </>
        )}
        <dl className="data-fields">
          {entries.map(([key, item]) => (
            <div className="data-field" key={key}>
              <dt>{key}</dt>
              <dd>
                <ReadableValue
                  value={
                    truncated && key === "preview"
                      ? readablePreview(item)
                      : item
                  }
                  field={key}
                />
              </dd>
            </div>
          ))}
        </dl>
      </>
    ) : (
      <span className="data-empty">Empty object</span>
    );
  }
  if (value === "") return <span className="data-empty">Empty text</span>;
  const code = !!field && /^(diff|code|patch)$/i.test(field);
  if (
    typeof value === "string" &&
    (value.length > 1200 || value.split("\n").length > 18)
  ) {
    return <LongText text={value} code={code} />;
  }
  return (
    <div className={`data-text${code ? " mono" : ""}`}>{String(value)}</div>
  );
}

export function RecordedData({
  value,
  attributes = false,
}: {
  value: unknown;
  attributes?: boolean;
}) {
  const [raw, setRaw] = useState(false);
  const missing = value === null || value === undefined;
  const emptyAttributes =
    attributes &&
    !missing &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    Object.keys(value).length === 0;

  return (
    <div className={`recorded-data${attributes ? " data-attributes" : ""}`}>
      <div className="data-toolbar">
        <span>{raw ? "Original JSON" : "Readable view"}</span>
        <button
          className="small-button"
          type="button"
          aria-pressed={raw}
          onClick={() => setRaw(!raw)}
        >
          {raw ? "View readable" : "View JSON"}
        </button>
      </div>
      {raw ? (
        <pre>{JSON.stringify(value ?? null, null, 2)}</pre>
      ) : missing || emptyAttributes ? (
        <div className="empty-value">
          {attributes ? "No attributes recorded." : "No value recorded."}
        </div>
      ) : (
        <ReadableValue value={value} />
      )}
    </div>
  );
}
