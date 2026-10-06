import { useEffect, useState } from "react";

type ApiStatus = "checking" | "connected" | "unavailable";

const apiUrl = import.meta.env.VITE_API_URL ?? "http://localhost:8000/api/v1";

function App() {
  const [apiStatus, setApiStatus] = useState<ApiStatus>("checking");

  useEffect(() => {
    const controller = new AbortController();

    fetch(`${apiUrl}/health/live`, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) {
          throw new Error("API health check failed");
        }
        setApiStatus("connected");
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        setApiStatus("unavailable");
      });

    return () => controller.abort();
  }, []);

  return (
    <main>
      <section className="hero" aria-labelledby="page-title">
        <div className="eyebrow">AI evaluation &amp; reliability</div>
        <h1 id="page-title">TraceGrade</h1>
        <p className="lede">
          Trace applications, run durable evaluations, compare versions, and catch
          regressions before they reach users.
        </p>
        <div className={`status status--${apiStatus}`} role="status">
          <span aria-hidden="true" />
          API {apiStatus}
        </div>
      </section>

      <section className="foundation" aria-labelledby="foundation-title">
        <p className="section-label">Phase 1</p>
        <h2 id="foundation-title">Foundation is ready.</h2>
        <p>
          Product workflows will appear here as each frozen phase is completed. The
          scope is intentionally fixed.
        </p>
      </section>
    </main>
  );
}

export default App;
