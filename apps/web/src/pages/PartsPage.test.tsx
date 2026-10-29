import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { PurchaseParts } from "./PartsPage.tsx";

describe("Parts purchase states", () => {
  it("shows owned separately and excludes it from the to-buy amount", () => {
    const html = renderToStaticMarkup(<PurchaseParts purchases={{
      lines: [
        { component: "pi", type: "pi5", owned: true, status: "owned", note: "workbench", purchase_cost: 0, currency: null },
        { component: "motor", type: "motor", owned: false, status: "sourced", note: "", purchase_cost: 20, currency: "USD" },
        { component: "servo", type: "servo", owned: false, status: "to_buy", note: "", purchase_cost: null, currency: null },
      ], to_buy_totals: { USD: 20 }, unpriced_to_buy: 1, to_buy_count: 2,
    }} />);
    expect(html).toContain("Owned");
    expect(html).toContain("Sourced");
    expect(html).toContain("To buy: 20.00 USD · 1 unpriced");
    expect(html).toContain("Unpriced");
  });
});
