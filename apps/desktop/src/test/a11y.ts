import axe from "axe-core";

/** Runs axe-core against a rendered container. Layout-dependent rules (contrast, target size) need a real browser. */
export async function accessibilityViolations(container: Element): Promise<string[]> {
  const results = await axe.run(container, {
    rules: { "color-contrast": { enabled: false }, "target-size": { enabled: false }, region: { enabled: false } },
  });
  return results.violations.map((v) => `${v.id}: ${v.help} (${v.nodes.map((n) => n.target.join(" ")).join(", ")})`);
}
