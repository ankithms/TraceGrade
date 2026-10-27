import { Icon } from "./Icon";

export function Welcome({ onConnect }: { onConnect: () => void }) {
  return (
    <section className="welcome" aria-labelledby="welcome-title">
      <div className="welcome-copy">
        <div className="eyebrow">Clarity at every step</div>
        <h1 id="welcome-title">Understand every run.</h1>
        <p>
          From the first request to the final response. See what happened,
          <br className="desktop-break" /> inspect each model call, and find the
          details that matter.
        </p>
        <button className="button primary" onClick={onConnect}>
          Connect your project <Icon name="arrow" />
        </button>
        <small>Get started with your project API key.</small>
      </div>
      <div
        className="preview"
        aria-label="Illustrative example of a trace inspector, not live project data"
      >
        <div className="preview-heading">
          <span>
            <Icon name="trace" /> Code review pipeline
          </span>
          <span className="example-label">Example trace</span>
        </div>
        <div className="preview-body">
          <div className="preview-tree">
            <div className="preview-label">Execution</div>
            <div className="preview-node">
              <Icon name="operation" />
              <span>Review pipeline</span>
              <span className="preview-time">2.4 s</span>
            </div>
            <div className="preview-children">
              <div className="preview-node">
                <Icon name="check" />
                <span>Load diff</span>
                <span className="preview-time">120 ms</span>
              </div>
              <div className="preview-node preview-selected">
                <Icon name="model" />
                <span>
                  Model review<small>Language model</small>
                </span>
                <span className="preview-time">2.1 s</span>
              </div>
              <div className="preview-node">
                <Icon name="check" />
                <span>Publish review</span>
                <span className="preview-time">180 ms</span>
              </div>
            </div>
            <div className="preview-timeline">
              <span>0 s</span>
              <i />
              <span>2.4 s</span>
            </div>
          </div>
          <div className="preview-inspector">
            <div className="preview-inspector-heading">
              <div>
                <div className="preview-label">Model call</div>
                <h2>Model review</h2>
              </div>
              <span className="badge ok">
                <i />
                Success
              </span>
            </div>
            <div className="preview-data-label">Input</div>
            <p className="preview-input">
              Review this pull request for correctness and suggest improvements.
            </p>
            <div className="preview-data-label">Output</div>
            <p className="preview-output">
              The changes look good. Add a check for an empty response before
              accessing the result.
            </p>
            <div className="preview-usage">
              <span>
                <strong>1,284</strong> tokens
              </span>
              <span>
                <strong>2.1 s</strong> duration
              </span>
              <span className="preview-recorded">
                <Icon name="check" /> Response captured
              </span>
            </div>
          </div>
        </div>
      </div>
      <div className="welcome-features">
        <div>
          <Icon name="layers" />
          <div>
            <h3>The complete picture</h3>
            <p>Follow every nested operation.</p>
          </div>
        </div>
        <div>
          <Icon name="model" />
          <div>
            <h3>Inside the model call</h3>
            <p>Inputs, outputs, and reported usage.</p>
          </div>
        </div>
        <div>
          <Icon name="search" />
          <div>
            <h3>A path to the problem</h3>
            <p>Find failed steps and captured errors.</p>
          </div>
        </div>
      </div>
    </section>
  );
}
