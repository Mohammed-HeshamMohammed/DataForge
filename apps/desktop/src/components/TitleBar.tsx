import { windowAction, type Theme } from "../lib/desktop.ts";
import { CloseIcon, ContrastIcon, DownloadIcon, LogoIcon, MinimizeIcon } from "./icons.tsx";

export function TitleBar({ theme, onToggleTheme, updateAvailable = false, onUpdate }: { theme: Theme; onToggleTheme: () => void; updateAvailable?: boolean; onUpdate: () => void }) {
  return (
    <header className="titlebar" data-tauri-drag-region>
      <div className="titlebar-brand" data-tauri-drag-region>
        <LogoIcon size={18} className="titlebar-logo" />
        <span data-tauri-drag-region>DataForge</span>
      </div>
      <div className="titlebar-actions">
        <button type="button" className="titlebar-btn" onClick={onToggleTheme} title={`Switch to ${theme === "dark" ? "light" : "dark"} theme`} aria-label="Toggle theme">
          <ContrastIcon size={13} strokeWidth={1.8} />
        </button>
        <button type="button" className="titlebar-btn" onClick={onUpdate} title={updateAvailable ? "A verified update is available" : "Check for updates"} aria-label="Updates">
          <DownloadIcon size={13} strokeWidth={1.8} />
          {updateAvailable && <span className="titlebar-dot" aria-hidden="true" />}
        </button>
        <button type="button" className="titlebar-btn" onClick={() => void windowAction("minimize")} aria-label="Minimize">
          <MinimizeIcon size={13} strokeWidth={1.8} />
        </button>
        <button type="button" className="titlebar-btn titlebar-close" onClick={() => void windowAction("close")} aria-label="Close">
          <CloseIcon size={13} strokeWidth={1.8} />
        </button>
      </div>
    </header>
  );
}
