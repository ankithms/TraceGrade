import { Icon } from "./Icon";

export function Welcome({ onConnect }: { onConnect: () => void }) {
  return (
    <>
      <div className="page-heading">
        <div className="eyebrow">Your workspace</div>
        <h1>Traces</h1>
        <p>Every step of your application, in one place.</p>
      </div>
      <section className="welcome" aria-labelledby="welcome-title">
        <div className="welcome-copy">
          <div className="welcome-icon">
            <Icon name="trace" />
          </div>
          <h2 id="welcome-title">
            A clear view of
            <br />
            every run.
          </h2>
          <p>
            Connect a project to follow its execution, inspect model calls, and
            find what needs your attention.
          </p>
          <button className="button primary" onClick={onConnect}>
            Connect your project <Icon name="arrow" />
          </button>
          <small>All you need is your project API key.</small>
        </div>
        <div
          className="welcome-diagram"
          aria-label="Example trace: a review pipeline with three nested operations"
        >
          <div className="diagram-header">
            <Icon name="layers" /> Example trace <span>Code review</span>
          </div>
          <div className="diagram-node">
            <Icon name="operation" />
            <span>Review pipeline</span>
            <i>
              <Icon name="check" /> Success
            </i>
          </div>
          <div className="diagram-children">
            <div>
              <Icon name="operation" />
              <span>Load diff</span>
              <Icon name="check" />
            </div>
            <div className="model-node">
              <Icon name="model" />
              <span>
                Model review<small>Input, output & token usage</small>
              </span>
              <Icon name="check" />
            </div>
            <div>
              <Icon name="operation" />
              <span>Publish review</span>
              <Icon name="check" />
            </div>
          </div>
          <p>From the first operation to the final response.</p>
        </div>
      </section>
      <div className="welcome-features">
        <div>
          <Icon name="layers" />
          <h3>Follow the execution</h3>
          <p>See how each operation connects in a nested span hierarchy.</p>
        </div>
        <div>
          <Icon name="model" />
          <h3>Inspect the model call</h3>
          <p>Read captured inputs, responses, and reported token usage.</p>
        </div>
        <div>
          <Icon name="search" />
          <h3>Find what went wrong</h3>
          <p>Locate failed steps and inspect their recorded errors.</p>
        </div>
      </div>
    </>
  );
}
