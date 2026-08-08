const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("src/schemii/web/app.js", "utf8");
const start = source.indexOf("function elementHasTruncatedText");
const end = source.indexOf("function positionTooltip", start);
assert.notEqual(start, -1, "truncated tooltip helper marker is missing");
assert.notEqual(end, -1, "truncated tooltip helper end marker is missing");

const body = {};
const style = { textOverflow: "ellipsis", webkitLineClamp: "none" };
const context = vm.createContext({
  document: { body },
  getComputedStyle: () => style
});
vm.runInContext(`
  ${source.slice(start, end)}
  globalThis.elementHasTruncatedText = elementHasTruncatedText;
  globalThis.automaticTooltipText = automaticTooltipText;
  globalThis.findAppTooltipTarget = findAppTooltipTarget;
`, context);

const element = {
  hidden: false,
  scrollWidth: 160,
  clientWidth: 80,
  scrollHeight: 20,
  clientHeight: 20,
  textContent: "  A long   database object name  ",
  dataset: {},
  parentElement: body,
  getAttribute: () => null
};

assert.equal(context.elementHasTruncatedText(element), true);
assert.equal(context.automaticTooltipText(element), "A long database object name");
assert.equal(context.findAppTooltipTarget(element), element);
assert.equal(element.dataset.tooltip, "A long database object name");
assert.equal(element.dataset.tooltipAutomatic, "true");

element.textContent = "Updated truncated text";
assert.equal(context.findAppTooltipTarget(element), element);
assert.equal(element.dataset.tooltip, "Updated truncated text");

element.scrollWidth = 80;
assert.equal(context.findAppTooltipTarget(element), null);
assert.equal("tooltip" in element.dataset, false);
assert.equal("tooltipAutomatic" in element.dataset, false);

element.dataset.tooltip = "Explicit tooltip";
assert.equal(context.findAppTooltipTarget(element), element);
assert.equal(element.dataset.tooltip, "Explicit tooltip");

style.textOverflow = "clip";
element.dataset = {};
element.scrollWidth = 160;
assert.equal(context.elementHasTruncatedText(element), false);

element.value = "Truncated input value";
style.textOverflow = "ellipsis";
assert.equal(context.automaticTooltipText(element), "Truncated input value");

console.log("Automatic tooltip tests passed");
