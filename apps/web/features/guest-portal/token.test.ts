import assert from "node:assert/strict";
import { test } from "node:test";

import { directionsUrl, isPlausibleToken, shortTime, tokenFromHash } from "./token.ts";

const TOKEN = "q3Vt0nZQd4u9s0X1yZb2c3D4e5F6g7H8i9J0kLmN-_x";

test("reads the token from the URL fragment only when well-formed", () => {
  assert.equal(tokenFromHash(`#token=${TOKEN}`), TOKEN);
  assert.equal(tokenFromHash(`token=${TOKEN}`), TOKEN);
  assert.equal(tokenFromHash("#token=short"), null);
  assert.equal(tokenFromHash("#token=<script>alert(1)</script>xxxxxxxxxxxxxxx"), null);
  assert.equal(tokenFromHash(""), null);
  assert.equal(tokenFromHash("#other=1"), null);
});

test("plausible token check", () => {
  assert.ok(isPlausibleToken(TOKEN));
  assert.ok(!isPlausibleToken(null));
  assert.ok(!isPlausibleToken("has spaces in it but is long enough"));
});

test("directions link encodes the address", () => {
  assert.equal(
    directionsUrl("Prinsengracht 263, Amsterdam"),
    "https://www.google.com/maps/dir/?api=1&destination=Prinsengracht%20263%2C%20Amsterdam",
  );
});

test("house times render as HH:MM", () => {
  assert.equal(shortTime("15:00:00"), "15:00");
  assert.equal(shortTime("11:00"), "11:00");
});
