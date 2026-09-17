import type { Command } from "../lib/commands.ts";
import { windowAction, type Theme } from "../lib/desktop.ts";
import { MenuBar } from "./MenuBar.tsx";
import { CommandCenter } from "./CommandPalette.tsx";
import { CloseIcon, ContrastIcon, DownloadIcon, GearIcon, LogoIcon, MaximizeIcon, MinimizeIcon } from "./icons.tsx";

export type TitleProject = { name: string; path: string };

export function TitleBar({
  theme,
  onToggleTheme,
  updateAvailable = false,
  onUpdate,
  commands = [],
  project = null,
  onSettings,
  commandCenterOpen = false,
  onCommandCenterChange,
}: {
  theme: Theme;
  onToggleTheme: () => void;
  updateAvailable?: boolean;
  onUpdate: () => void;
  commands?: Command[];
  project?: TitleProject | null;
  onSettings?: () => void;
  commandCenterOpen?: boolean;
  onCommandCenterChange?: (open: boolean) => void;
}) {
  return (
    <header className="titlebar" data-tauri-drag-region onDoubleClick={(e) => e.target === e.currentTarget && void windowAction("toggleMaximize")}>
      <div className="titlebar-brand" data-tauri-drag-region>
        <LogoIcon size={17} className="titlebar-logo" />
        <span className="titlebar-name" data-tauri-drag-region>
          DataForge
        </span>
      </div>
      {commands.length > 0 && <MenuBar commands={commands} />}
      <div className="titlebar-center" data-tauri-drag-region>
        {onCommandCenterChange && <CommandCenter commands={commands} project={project} open={commandCenterOpen} onOpenChange={onCommandCenterChange} />}
      </div>
      <div className="titlebar-actions">
        {onSettings && (
          <button type="button" className="titlebar-btn" onClick={onSettings} title="Settings (Ctrl+,)" aria-label="Settings">
            <GearIcon size={14} strokeWidth={1.8} />
          </button>
        )}
        <button type="button" className="titlebar-btn" onClick={onToggleTheme} title={`Switch to ${theme === "dark" ? "light" : "dark"} theme`} aria-label="Toggle theme">
          <ContrastIcon size={13} strokeWidth={1.8} />
        </button>
        <button type="button" className="titlebar-btn" onClick={onUpdate} title={updateAvailable ? "A verified update is available" : "Check for updates"} aria-label="Updates">
          <DownloadIcon size={13} strokeWidth={1.8} />
          {updateAvailable && <span className="titlebar-dot" aria-hidden="true" />}
        </button>
        <span className="titlebar-divider" aria-hidden="true" />
        <button type="button" className="titlebar-btn" onClick={() => void windowAction("minimize")} aria-label="Minimize">
          <MinimizeIcon size={13} strokeWidth={1.8} />
        </button>
        <button type="button" className="titlebar-btn" onClick={() => void windowAction("toggleMaximize")} aria-label="Maximize">
          <MaximizeIcon size={12} strokeWidth={1.8} />
        </button>
        <button type="button" className="titlebar-btn titlebar-close" onClick={() => void windowAction("close")} aria-label="Close">
          <CloseIcon size={13} strokeWidth={1.8} />
        </button>
      </div>
    </header>
  );
}
