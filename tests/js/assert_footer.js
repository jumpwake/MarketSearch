'use strict';
// Runs after dom_shim.js and the literal `_JS` text have executed. `_JS`
// ends with `apply();`, so by the time we get here the page has already
// been laid out once with life.value === '6000' (set by dom_shim.js).
// `apply` and `picksHost` are visible here because this file is
// concatenated — not required as a module — into the same script Node
// executes, so everything shares one top-level scope.

const order6000 = pickOrder();
const values6000 = pickValues();

// Simulate dragging the slider to 10,000: set the input's value and invoke
// the exact function the 'input' listener calls. No re-parenting or
// re-registration — this is the same `apply` the real page wires up.
life.value = '10000';
apply();

const order10000 = pickOrder();
const values10000 = pickValues();

console.log(JSON.stringify({order6000, values6000, order10000, values10000}));
