export type ErrorDescription = {
  title: string;
  nextStep: string;
  technical: string;
};

export function toErrorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (typeof error === "string") return error;
  return "An unexpected error occurred.";
}

export function describeError(message: string): ErrorDescription {
  const text = message.trim() || "An unexpected error occurred.";
  const lower = text.toLowerCase();

  if (lower.includes("bridge") || lower.includes("service is unavailable") || lower.includes("failed to fetch")) {
    return { title: "DataForge could not start its local service", nextStep: "Restart DataForge. If the problem continues, open Help → Logs and share the newest log file.", technical: text };
  }
  if (lower.includes("no dataforge project") || lower.includes("project path") || lower.includes("project is required")) {
    return { title: "That project could not be opened", nextStep: "Choose a DataForge project folder, or create a new project in an empty folder.", technical: text };
  }
  if (lower.includes("captcha") || lower.includes("challenge") || lower.includes("paywall") || lower.includes("access denied")) {
    return { title: "The website blocked automated collection", nextStep: "Do not bypass the block. Use an official API, export, licensed dataset, or another source that permits collection.", technical: text };
  }
  if (lower.includes("login")) {
    return { title: "The website requires a sign-in", nextStep: "Open the page in Scrape Studio and sign in yourself. DataForge never reads or stores page credentials, and collection still requires authorization.", technical: text };
  }
  if (lower.includes("robots") || lower.includes("not permitted") || lower.includes("policy")) {
    return { title: "This source does not allow the requested collection", nextStep: "Check the source terms and robots rules, or use an official API or download offered by the source.", technical: text };
  }
  if (lower.includes("rate limit") || lower.includes("too many requests") || lower.includes("429")) {
    return { title: "The source asked DataForge to slow down", nextStep: "Wait a few minutes, then try again. DataForge will keep the source's safe request limits.", technical: text };
  }
  if (lower.includes("csv") || lower.includes("xlsx") || lower.includes("json") || lower.includes("import path") || lower.includes("file")) {
    return { title: "DataForge could not read that file", nextStep: "Check that the file still exists and uses a supported data format, then try again.", technical: text };
  }
  return { title: "DataForge could not complete that action", nextStep: "Check the details below, correct the highlighted input if possible, and try again.", technical: text };
}
