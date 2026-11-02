import assert from "node:assert/strict";
import { test } from "node:test";

import {
  MODULES,
  escapeClosesPane,
  focusReturnTarget,
  moduleLabel,
  parseHash,
  roveIndex,
  routeHash,
  sameRoute,
} from "./panes.ts";

test("Esc closes the pane unless something more specific already took it", () => {
  assert.ok(escapeClosesPane({ key: "Escape", defaultPrevented: false }));
  assert.ok(!escapeClosesPane({ key: "Escape", defaultPrevented: true }), "palette handled it");
  assert.ok(!escapeClosesPane({ key: "Escape", defaultPrevented: false, isComposing: true }));
  assert.ok(!escapeClosesPane({ key: "Enter", defaultPrevented: false }));
});

test("focus goes back to the opener, or a fallback when the opener is gone", () => {
  const opener = { isConnected: true };
  const gone = { isConnected: false };
  const fallback = { isConnected: true };
  assert.equal(focusReturnTarget(opener, fallback), opener);
  assert.equal(focusReturnTarget(gone, fallback), fallback);
  assert.equal(focusReturnTarget(null, fallback), fallback);
});

test("every dock module that is built opens from its own hash", () => {
  for (const m of MODULES.filter((x) => !x.soon)) {
    assert.deepEqual(parseHash(`#${m.id}`), { kind: "module", id: m.id });
    assert.equal(routeHash({ kind: "module", id: m.id }), `#${m.id}`);
  }
});

test("the old rail hashes and friendly names still deep-link", () => {
  assert.deepEqual(parseHash("#/settings"), { kind: "module", id: "settings" });
  assert.deepEqual(parseHash("#/schedule"), { kind: "module", id: "schedule" });
  assert.deepEqual(parseHash("#jobs"), { kind: "module", id: "schedule" });
  assert.deepEqual(parseHash("#audit"), { kind: "module", id: "activity" });
  assert.deepEqual(parseHash("#Memory"), { kind: "module", id: "brain" });
});

test("empty, console, unknown and not-yet-built hashes are home, never blank", () => {
  for (const hash of ["", "#", "#/", "#console", "#nope", "#proposals", "#agent/"]) {
    assert.deepEqual(parseHash(hash), { kind: "home" }, hash);
  }
});

test("agent routes accept real names only", () => {
  assert.deepEqual(parseHash("#agent/system-ops"), { kind: "agent", name: "system-ops" });
  assert.deepEqual(parseHash("#agent/orchestrator"), { kind: "agent", name: "orchestrator" });
  assert.deepEqual(parseHash("#agent/../etc"), { kind: "home" });
  assert.deepEqual(parseHash("#agent/<script>"), { kind: "home" });
  assert.equal(routeHash({ kind: "agent", name: "research" }), "#agent/research");
});

test("home has no hash and routes compare by hash", () => {
  assert.equal(routeHash({ kind: "home" }), "");
  assert.ok(sameRoute(parseHash("#/files"), parseHash("#files")));
  assert.ok(!sameRoute(parseHash("#files"), parseHash("#apps")));
});

test("the Proposals slot exists in the dock but opens nothing yet", () => {
  assert.ok(MODULES.some((m) => m.id === "proposals" && m.soon));
  assert.equal(moduleLabel("proposals"), "Proposals");
});

test("roving focus wraps and honours Home/End", () => {
  assert.equal(roveIndex(0, "ArrowLeft", 5), 4);
  assert.equal(roveIndex(4, "ArrowRight", 5), 0);
  assert.equal(roveIndex(2, "Home", 5), 0);
  assert.equal(roveIndex(2, "End", 5), 4);
  assert.equal(roveIndex(2, "a", 5), null);
  assert.equal(roveIndex(0, "ArrowRight", 0), null);
});
