// node --test — the settings screen's pure helpers. The DOM half is driven
// by QA against a running instance; these are the parts that hold numbers.
import assert from "node:assert/strict";
import { test } from "node:test";

import { bars, esc, friendly, nextValue } from "./unit-screen.js";

test("a stepper moves one server-given step and clamps to the server's bounds", () => {
  const caption = { min: 0.5, max: 2, step: 0.1 };
  assert.equal(nextValue(caption, 1.5, 1), 1.6);
  assert.equal(nextValue(caption, 1.5, -1), 1.4);
  assert.equal(nextValue(caption, 2, 1), 2);
  assert.equal(nextValue(caption, 0.5, -1), 0.5);
  assert.equal(nextValue(caption, 0.7, -1), 0.6, "no float dust: 0.7 - 0.1 is 0.6");
  const birds = { min: 1, max: 12, step: 1 };
  assert.equal(nextValue(birds, 12, 1), 12);
  assert.equal(nextValue(birds, 1, -1), 1);
});

test("the hours wrap instead of clamping", () => {
  const hours = { min: 0, max: 23, step: 1 };
  assert.equal(nextValue(hours, 23, 1, true), 0);
  assert.equal(nextValue(hours, 0, -1, true), 23);
  assert.equal(nextValue(hours, 7, 1, true), 8);
});

test("everything interpolated into markup is escaped", () => {
  assert.equal(esc(`<b>"x" & 'y'</b>`), "&lt;b&gt;&quot;x&quot; &amp; &#39;y&#39;&lt;/b&gt;");
  assert.equal(esc(42), "42");
});

test("signal bars light by quartile", () => {
  const lit = (svg) => (svg.match(/#4a3f2e/g) || []).length;
  assert.equal(lit(bars(90)), 4);
  assert.equal(lit(bars(60)), 3);
  assert.equal(lit(bars(30)), 2);
  assert.equal(lit(bars(5)), 1);
});

test("errors the owner can act on keep NetworkManager's words and lose the traceback's", () => {
  assert.equal(friendly("Error: Secrets were required, but not provided."), "Error: Secrets were required, but not provided.");
  assert.equal(friendly("could not run systemctl: FileNotFoundError"), "this unit can't do that from here");
  assert.equal(friendly("unit 500"), "the wall didn't answer");
});
