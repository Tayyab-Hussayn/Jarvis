import assert from "node:assert/strict";
import { test } from "node:test";

import { flatten, groupApps, moveIndex, sortWorkspaces } from "./workspaces.ts";

test("workspaces sort numerically, names after numbers", () => {
  assert.deepEqual(sortWorkspaces(["2", "1", "4", "3"]), ["1", "2", "3", "4"]);
  assert.deepEqual(sortWorkspaces(["10", "9", "web", "2", "special:magic"]), [
    "2", "9", "10", "special:magic", "web",
  ]);
  assert.deepEqual(sortWorkspaces(["1", "-98"]), ["-98", "1"]);
});

test("sortWorkspaces does not mutate its input", () => {
  const input = ["2", "1"];
  sortWorkspaces(input);
  assert.deepEqual(input, ["2", "1"]);
});

const apps = [
  { name: "Firefox" }, { name: "Foot" }, { name: "Blender", comment: "3D modelling" },
  { name: "Files" },
];

test("frequent apps come first in server order, rest alphabetical", () => {
  const g = groupApps(apps, ["Foot", "Firefox", "Gone"], "");
  assert.deepEqual(g.frequent.map((a) => a.name), ["Foot", "Firefox"]);
  assert.deepEqual(g.rest.map((a) => a.name), ["Blender", "Files"]);
  assert.deepEqual(flatten(g).map((a) => a.name), ["Foot", "Firefox", "Blender", "Files"]);
});

test("a search is one flat list: prefix, substring, then comment", () => {
  const g = groupApps(apps, ["Foot"], "fi");
  assert.equal(g.frequent.length, 0);
  assert.deepEqual(g.rest.map((a) => a.name), ["Files", "Firefox"]);
  assert.deepEqual(groupApps(apps, [], "3d").rest.map((a) => a.name), ["Blender"]);
  assert.deepEqual(groupApps(apps, [], "zzz").rest, []);
});

test("arrow keys wrap and start from either end", () => {
  assert.equal(moveIndex(-1, 1, 3), 0);
  assert.equal(moveIndex(-1, -1, 3), 2);
  assert.equal(moveIndex(2, 1, 3), 0);
  assert.equal(moveIndex(0, -1, 3), 2);
  assert.equal(moveIndex(0, 1, 0), -1);
});
