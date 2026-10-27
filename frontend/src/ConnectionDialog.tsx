import { useEffect, useRef } from "react";
import type { FormEvent } from "react";
import { motion, useIsPresent, useReducedMotion } from "motion/react";
import { Icon } from "./Icon";

interface Props {
  opener: HTMLElement | null;
  name: string;
  projectKey: string;
  connecting: boolean;
  error: string;
  onName: (value: string) => void;
  onKey: (value: string) => void;
  onClose: () => void;
  onSubmit: (event: FormEvent) => void;
}

export function ConnectionDialog({
  opener,
  name,
  projectKey,
  connecting,
  error,
  onName,
  onKey,
  onClose,
  onSubmit,
}: Props) {
  const dialog = useRef<HTMLElement>(null);
  const reducedMotion = useReducedMotion();
  const isPresent = useIsPresent();
  useEffect(() => {
    if (!isPresent) return;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    dialog.current?.querySelector("input")?.focus();
    return () => {
      document.body.style.overflow = overflow;
      opener?.focus();
    };
  }, [isPresent, opener]);

  return (
    <motion.div
      className="modal-backdrop"
      style={{ pointerEvents: isPresent ? "auto" : "none" }}
      aria-hidden={!isPresent}
      initial={{ opacity: 1 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: reducedMotion ? 0.1 : 0.16 }}
      onClick={(event) => {
        if (event.target === event.currentTarget && !connecting) onClose();
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape" && !connecting) {
          event.preventDefault();
          onClose();
        }
        if (event.key !== "Tab") return;
        const controls = dialog.current?.querySelectorAll<HTMLElement>(
          "button:not(:disabled), input:not(:disabled)",
        );
        if (!controls?.length) {
          event.preventDefault();
          dialog.current?.focus();
          return;
        }
        const first = controls[0],
          last = controls[controls.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }}
    >
      <motion.section
        ref={dialog}
        className="connection-modal"
        role="dialog"
        aria-modal="true"
        inert={!isPresent}
        aria-labelledby="connect-title"
        aria-describedby="connect-description"
        tabIndex={-1}
        initial={{
          opacity: 1,
          scale: reducedMotion ? 1 : 0.97,
          y: reducedMotion ? 0 : 12,
        }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{
          opacity: 0,
          scale: reducedMotion ? 1 : 0.97,
          y: reducedMotion ? 0 : 12,
        }}
        transition={
          reducedMotion
            ? { duration: 0.1 }
            : { type: "spring", bounce: 0, visualDuration: 0.25 }
        }
      >
        <form onSubmit={onSubmit}>
          <div className="modal-heading">
            <div className="welcome-icon">
              <Icon name="trace" />
            </div>
            <button
              type="button"
              className="icon-button"
              disabled={connecting}
              onClick={onClose}
              aria-label="Close connection dialog"
            >
              <Icon name="close" />
            </button>
          </div>
          <h2 id="connect-title">Connect project</h2>
          <p id="connect-description">
            Use your project’s API key to bring its traces into view.
          </p>
          <label>
            Project label
            <input
              required
              maxLength={80}
              autoComplete="off"
              placeholder="e.g. Code Review Assistant"
              value={name}
              onChange={(event) => onName(event.target.value)}
              disabled={connecting}
            />
          </label>
          <label>
            Project API key
            <input
              type="password"
              required
              autoComplete="off"
              spellCheck={false}
              placeholder="tg_…"
              value={projectKey}
              onChange={(event) => onKey(event.target.value)}
              disabled={connecting}
              aria-describedby="connection-note"
            />
          </label>
          <p className="connection-note" id="connection-note">
            <Icon name="shield" />
            <span>
              Your key can read only this project’s traces. Connections stay in
              memory; reloading clears your keys.
            </span>
          </p>
          {error && (
            <div className="error-box" role="alert">
              {error}
            </div>
          )}
          <button
            className="button primary"
            disabled={connecting || !name.trim() || !projectKey.trim()}
            type="submit"
          >
            {connecting ? (
              <>
                <span className="spinner" /> Connecting…
              </>
            ) : (
              <>
                Connect project <Icon name="arrow" />
              </>
            )}
          </button>
          {connecting && (
            <span className="sr-only" role="status">
              Connecting to your project…
            </span>
          )}
        </form>
      </motion.section>
    </motion.div>
  );
}
