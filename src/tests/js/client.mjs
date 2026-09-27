/*
  Runs the real app.js against the DOM in ./dom.mjs, driving it the way a reader
  does: the page is loaded, text is typed into the editor, the form is submitted,
  and the patches that python rendered for that flow arrive over a fake stream
  that can be stopped half way.

  Stopping it half way is the point.  "The failed status is pinned to the bottom
  of the feed" and "the sample only shows once the game is over" are both claims
  about the page at one moment during a run, and a harness that only ever looks
  at the end cannot see either of them.

  pytest runs this with node (see test_pass3_notes.py) and fails on a non-zero
  exit, so a bug in how the browser applies a patch fails the python suite.

  Timers are run by hand.  app.js types a long block out on a schedule, and the
  interesting question is how much of the text is on the page after N steps, so
  the harness has to be able to stop in the middle of one.
*/

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { createDocument } from "./dom.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
/* Overridable so a broken copy of app.js can be run against the same checks,
   which is how these checks are known to be worth having. */
const APP = process.env.APP_JS || join(HERE, "..", "..", "web", "static", "js", "app.js");

/* ------------------------------------------------------------- assertions -- */

const failures = [];
let checks = 0;

function ok(condition, message) {
  checks += 1;
  if (!condition) failures.push(message);
}

function equal(actual, expected, message) {
  ok(
    actual === expected,
    `${message}\n      expected: ${JSON.stringify(expected)}\n      actual:   ${JSON.stringify(actual)}`
  );
}

/* ------------------------------------------------------------------ timers -- */

let clock = 0;
let sequence = 0;
const scheduled = new Map();

function setTimeoutShim(handler, delay) {
  const id = ++sequence;
  scheduled.set(id, { at: clock + (delay || 0), handler, id });
  return id;
}

function clearTimeoutShim(id) {
  scheduled.delete(id);
}

/* Runs the due timers in order.  app.js re-arms its own timers from inside the
   handler, so one advance can type a whole long block out; ``steps`` bounds it,
   because the multiword cycles forever. */
function advance(steps) {
  for (let done = 0; done < steps; done += 1) {
    let next = null;
    for (const [id, entry] of scheduled) {
      if (!next || entry.at < next.entry.at || (entry.at === next.entry.at && id < next.id)) {
        next = { id, entry };
      }
    }
    if (!next) return;
    scheduled.delete(next.id);
    clock = next.entry.at;
    next.entry.handler();
  }
}

/* The fake stream resolves on the microtask queue, so draining it is letting the
   promise chain run.  Everything is already in hand, so this is "let the reader
   finish", not "wait for the network". */
async function settle(rounds = 400) {
  for (let i = 0; i < rounds; i += 1) await Promise.resolve();
}

/* ------------------------------------------------------------------- page -- */

function newPage(bodyHtml) {
  const document = createDocument(bodyHtml);
  const requests = [];
  const queues = [];
  const encoder = new TextEncoder();
  /* Set by ``offline()``: after that nothing answers, which is what a page looks
     like when the server is not running or the request was blocked. */
  let offline = false;

  /* One gate per stream being read.  Open means every read resolves at once;
     held means reads park until the harness lets one through.  A run in flight
     and a toggle posted into it are two streams at the same time, so the gates
     are addressed by which request they belong to rather than as one. */
  const gates = [];
  const claimed = [];

  function makeReader(frames, gate) {
    let at = 0;
    return {
      read() {
        return new Promise((resolve) => {
          const send = () => {
            if (at >= frames.length) {
              resolve({ done: true, value: undefined });
              return;
            }
            const frame = "data: " + JSON.stringify(frames[at]) + "\n\n";
            at += 1;
            resolve({ done: false, value: encoder.encode(frame) });
          };
          if (gate.open) send();
          else gate.waiting.push(send);
        });
      },
    };
  }

  /* An answer that is not a stream: a status, and the body behind it, which is
     what the server sends when a run will not start. */
  function answer(status, text) {
    return {
      ok: status >= 200 && status < 300,
      status,
      text: () => Promise.resolve(text),
      json: () => Promise.resolve(JSON.parse(text)),
      body: null,
    };
  }

  function fetchShim(path, options) {
    requests.push({
      path,
      method: options ? options.method : "GET",
      body: options && options.body ? JSON.parse(options.body) : null,
    });
    /* Nothing answers at all: the request never reaches the server, which the
       browser says with a TypeError and no other information. */
    if (offline) return Promise.reject(new TypeError("Failed to fetch"));
    /* The page asks this after anything goes wrong, and it is never a stream. */
    if (path === "/api/diagnostics") {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(page.diagnostics),
      });
    }
    const queued = queues.shift();
    if (queued && !Array.isArray(queued)) {
      return Promise.resolve(answer(queued.status, queued.body || ""));
    }
    const frames = queued || [];
    const gate = claimed.shift() || { open: true, waiting: [] };
    gates.push(gate);
    return Promise.resolve({
      ok: true,
      status: 200,
      body: { getReader: () => makeReader(frames, gate) },
    });
  }

  /* Let exactly one more frame through on a given stream, then hold it again. */
  function deliver(index, all) {
    const gate = gates[index];
    if (!gate) return;
    gate.open = true;
    const waiting = gate.waiting.splice(0);
    if (!all) gate.open = false;
    for (const send of waiting) send();
  }

  const documentListeners = new Map();
  const realAdd = document.addEventListener.bind(document);
  document.addEventListener = function (type, handler) {
    if (!documentListeners.has(type)) documentListeners.set(type, []);
    documentListeners.get(type).push(handler);
    realAdd(type, handler);
  };
  /* An event the way the browser delivers one: the target's own handlers, then
     the document's.  app.js only ever listens on the document. */
  document.fire = function (type, event) {
    const target = event.target;
    for (const handler of [...(target.listeners.get(type) || [])]) handler(event);
    for (const handler of [...(documentListeners.get(type) || [])]) handler(event);
  };

  /* The window the same way: app.js hangs two hooks off it, and the harness has
     to be able to set either of them off. */
  const windowListeners = new Map();
  const windowShim = {
    setTimeout: setTimeoutShim,
    clearTimeout: clearTimeoutShim,
    scrollTo() {},
    addEventListener(type, handler) {
      if (!windowListeners.has(type)) windowListeners.set(type, []);
      windowListeners.get(type).push(handler);
    },
  };

  /* app.js writes what it could not fix to the console, so the harness keeps a
     copy: a report nobody can read is the same as no report.  ``warn`` and
     ``error`` are passed through to the real console as well, so a genuine
     mistake in the page still shows up in the output rather than only here. */
  const log = [];
  const realConsole = { log: console.log, info: console.info, warn: console.warn, error: console.error };
  const consoleShim = {};
  for (const level of ["log", "info", "warn", "error"]) {
    consoleShim[level] = (...args) => {
      log.push({ level, args });
      if (level === "warn" || level === "error") realConsole[level](...args);
    };
  }

  const globals = {
    window: windowShim,
    document,
    navigator: { clipboard: null },
    fetch: fetchShim,
    console: consoleShim,
    location: { href: "http://localhost:5000/" },
    Audio: class {
      constructor(src) {
        this.src = src;
      }
      play() {
        return Promise.resolve();
      }
    },
    TextDecoder,
    Blob: class {
      constructor(parts) {
        this.parts = parts;
      }
    },
    URL: { createObjectURL: () => "blob:stub", revokeObjectURL() {} },
    FileReader: class {},
    setTimeout: setTimeoutShim,
    clearTimeout: clearTimeoutShim,
  };
  const saved = {};
  for (const [name, value] of Object.entries(globals)) {
    /* Some of these - navigator in particular - are getter-only on the global, so
       they have to be redefined rather than assigned. */
    saved[name] = Object.getOwnPropertyDescriptor(globalThis, name);
    Object.defineProperty(globalThis, name, {
      value,
      configurable: true,
      writable: true,
    });
  }

  /* Indirect eval, so the file's own "use strict" IIFE runs here with the globals
     above in place. */
  (0, eval)(readFileSync(APP, "utf8"));

  async function run() {
    advance(400);
    await settle();
  }

  const page = {
    document,
    requests,
    /* Everything app.js wrote to the console while this page was open. */
    console: log,
    /* What the server says when the page asks what it knows.  A page with no key
       is the case being reproduced. */
    diagnostics: {
      api: "real",
      client_built: false,
      key_found: false,
      key_source: "missing",
      calls: [],
      failures: 1,
    },

    /* The patches the next request will answer with.  ``hold`` stops the stream
       at its first frame, so the harness can look at the page mid run. */
    stream(frames, hold = false) {
      queues.push(frames);
      claimed.push({ open: !hold, waiting: [] });
    },

    /* The next request is refused with this status and body, instead of being
       answered with patches. */
    refuse(status, body) {
      queues.push({ status, body });
    },

    /* Nothing answers anything from now on. */
    offline(value = true) {
      offline = value;
    },

    /* An event on the window, as the browser would deliver it. */
    fireWindow(type, event) {
      for (const handler of [...(windowListeners.get(type) || [])]) handler(event);
    },

    async submit(text) {
      const area = document.querySelector(".editor:not(.is-submitted) .editor__input");
      if (!area) throw new Error("there is no editor accepting input");
      area.value = text;
      const form = area.closest(".editor");
      document.fire("submit", { target: form, preventDefault() {} });
      await settle();
      await run();
    },

    /* Lets exactly one more frame through on a stream, and waits for it.  With
       ``types`` off the clock is left alone, so a block can be caught halfway
       through being written and then updated again. */
    async step(index, types = true) {
      deliver(index, false);
      if (types) await run();
      else await settle();
    },

    /* Lets the typing catch up by a fixed number of timer steps. */
    async type(steps) {
      advance(steps);
      await settle();
    },

    /* Lets the rest of a stream through. */
    async release(index) {
      deliver(index, true);
      await run();
    },

    /* A click on a checkbox ticks it first, the way the browser does it, so the
       handler sees the new state rather than the old one. */
    click(node) {
      if (node.tagName === "INPUT" && node.getAttribute("type") === "checkbox") {
        node.checked = !node.checked;
      }
      document.fire("click", { target: node, preventDefault() {} });
    },

    async settle() {
      await run();
    },

    restore() {
      for (const [name, descriptor] of Object.entries(saved)) {
        if (descriptor) Object.defineProperty(globalThis, name, descriptor);
        else delete globalThis[name];
      }
    },
  };

  return page;
}

/* ----------------------------------------------------------------- shapes -- */

function feedEntries(document) {
  const feed = document.querySelector("[data-feed-inner]");
  return feed ? feed.children : [];
}

function idsOf(document) {
  return feedEntries(document).map((node) => node.getAttribute("data-entry-id"));
}

function* inOrder(node) {
  for (const child of node.children) {
    yield child;
    yield* inOrder(child);
  }
}

const STATUSES = new Set([
  "StatusInterpreting", "StatusCoding", "StatusDeploying",
  "StatusPlaying", "StatusCollecting", "StatusQuestion",
]);

/* The parts of a status row after its label, in the order they are on screen. */
function rowOrder(document, id) {
  const entry = document.querySelector(`[data-entry-id="${id}"]`);
  if (!entry) return [];
  const row = entry.querySelector(".status__row") || entry;
  return [...inOrder(row)]
    .map((node) => node.getAttribute("data-component"))
    .filter((component) => component && !STATUSES.has(component));
}

function typedState(document, id) {
  const code = document.querySelector(`[data-entry-id="${id}"] .typed`);
  return code ? code.getAttribute("data-typed") : null;
}

function typedText(document, id) {
  const code = document.querySelector(`[data-entry-id="${id}"] .typed`);
  return code ? code.textContent : null;
}

function longBlocks(document) {
  return document.querySelectorAll('[data-component="LongBlock"]');
}

function entry(document, id) {
  return document.querySelector(`[data-entry-id="${id}"]`);
}

/* The feed's own order, and the statuses by what they are rather than by what
   they happen to be called - the ids are counters, not names. */
function statusIds(document, kind) {
  return feedEntries(document)
    .filter((node) => node.getAttribute("data-status") === kind)
    .map((node) => node.getAttribute("data-entry-id"));
}

function allStatusIds(document) {
  return feedEntries(document)
    .filter((node) => node.getAttribute("data-status") !== null)
    .map((node) => node.getAttribute("data-entry-id"));
}

/* ------------------------------------------------------------------- main -- */

const input = JSON.parse(readFileSync(process.argv[2], "utf8"));
const scenarios = input.scenarios;

/* --- 1. a vague prompt, the question it asks, and the answer ------------- */

{
  const page = newPage(input.page);
  const document = page.document;

  page.stream(scenarios.vague);
  await page.submit(scenarios.prompts.vague);

  const question = document.querySelector('[data-component="StatusQuestion"]');
  ok(question !== null, "the vague prompt asked a question");
  equal(question.querySelectorAll(".editor__input").length, 1, "the question has its own editor");

  /* The answer run, stopped with the swarm mid playtest - which is where a reader
     would press the override. */
  const answer = scenarios.answer;
  const playingIndex = answer.findIndex((patch) => patch.component === "StatusPlaying");

  page.stream(answer, true);
  await page.submit(scenarios.prompts.answer);
  ok(playingIndex !== -1, "the answer run reaches the player swarm");
  for (let i = 0; i <= playingIndex; i += 1) await page.step(1);

  /* -- the override, mid run -- */
  const override = document.querySelector("[data-override]");
  ok(override !== null, "there is a manual override in the playing status");
  if (override) {
    equal(override.tagName, "BUTTON", "the override is a real button");
    equal(override.disabled, false, "the override starts enabled");
    const row = override.closest('[data-component="StatusPlaying"]');
    equal(rowOrder(document, row.getAttribute("data-entry-id")).join(","),
      "ShowSample,SimOverride,Ellipses",
      "the playing row is label, then Show sample game, then the override, then the ellipses");
    page.stream([], true);
    page.click(override);
    await page.settle();
    equal(page.requests.filter((r) => r.path === "/api/override").length, 1,
      "the click asks the server to stop the swarm");
    equal(override.disabled, true, "the button says it is working rather than lying");
  }

  /* The design review arrives at the end of the run.  Step to it with the clock
     stopped, because "it looked finished before it was" is a claim about the
     moment it lands. */
  const reviewAt = answer.findIndex((patch) => patch.component === "LongBlockResponse");
  ok(reviewAt !== -1, "the answer run has a design review in it");
  for (let i = playingIndex + 1; i <= reviewAt; i += 1) await page.step(1, false);

  const landing = document.querySelector('[data-component="LongBlockResponse"]');
  ok(landing !== null, "the design review is in the feed");
  if (landing) {
    const id = landing.getAttribute("data-entry-id");
    const code = landing.querySelector(".typed");
    const shown = typedText(document, id);
    equal(typedState(document, id), "1", "the review is being written, not already finished");
    ok(shown.length > 0, "and something of it is on the page from the first step");
    ok(shown.length < code.getAttribute("data-full").length,
      "the whole review is not on the page the moment it lands");
    await page.type(20);
    const later = typedText(document, id);
    ok(later.length > shown.length, "and it keeps being written as the timers go by");
    ok(later.startsWith(shown), "without starting again from the top");
  }

  await page.release(1);
  await page.settle();

  /* -- the statuses, once the run is over -- */
  const ids = idsOf(document);
  const interpreting = statusIds(document, "interpreting");
  equal(interpreting.length, 2, "the second attempt gets its own Interpreting status");

  for (const id of ids) {
    equal(document.querySelectorAll(`[data-entry-id="${id}"]`).length, 1,
      `${id} is on the page exactly once`);
  }

  /* The failure belongs to the status that asked the question, and the newest
     status is the last one in the feed - not a leftover Interpreting that has
     been re-created at the bottom. */
  const failures = document.querySelectorAll('[data-result="failure"]');
  equal(failures.length, 1, "one failure, beside the status that asked the question");
  if (failures.length === 1) {
    equal(
      failures[0].closest('[data-component="StatusInterpreting"]').getAttribute("data-entry-id"),
      interpreting[0],
      "the failure is on the interpreting status that asked, not on a copy at the bottom"
    );
  }
  const statuses = allStatusIds(document);
  const lastStatus = statuses[statuses.length - 1];
  const lastStatusEntry = entry(document, lastStatus);
  equal(lastStatusEntry.getAttribute("data-status"), "collecting",
    "the last status in the feed is the newest one");
  equal(document.querySelectorAll('[data-status="running"]').length, 0,
    "nothing is left running once the run is over");

  /* -- the design review is written out, not blank -- */
  const response = document.querySelector('[data-component="LongBlockResponse"]');
  ok(response !== null, "the design review is in the feed");
  if (response) {
    const id = response.getAttribute("data-entry-id");
    const code = response.querySelector(".typed");
    equal(typedState(document, id), "2", "the design review finished typing itself out");
    equal(typedText(document, id), code.getAttribute("data-full"),
      "and what is on the page is all of it, not a prefix");
    ok(typedText(document, id).length > 0, "the design review is not blank");
  }

  const code = document.querySelector('[data-component="LongBlockCode"]');
  if (code) {
    const id = code.getAttribute("data-entry-id");
    equal(typedState(document, id), "2", "the code block finished typing itself out");
  }

  page.restore();
}

/* --- 2. the sample game, a turn at a time, while the swarm is playing ----- */

{
  const page = newPage(input.page);
  const document = page.document;
  const turn = scenarios.sample.turn;
  const toggle = scenarios.sample.toggle;

  page.stream(turn, true);
  await page.submit(scenarios.prompts.complete);

  /* Step to the point where the swarm is playing, and then wait there: this is
     where a reader ticks "Show sample game" for the first time. */
  const playingAt = turn.findIndex((patch) => patch.component === "StatusPlaying");
  ok(playingAt !== -1, "the run has a playing status");
  for (let i = 0; i <= playingAt; i += 1) await page.step(0);

  const playing = document.querySelector('[data-status="playing"]');
  equal(playing.getAttribute("data-state"), "running", "the swarm is playing");
  equal(
    document.querySelectorAll('[data-variant="sample"]').length,
    0,
    "an unticked box shows no game"
  );

  /* Ticking it mid run, which is the whole point of it being on that status. */
  const box = document.querySelector('[data-status="playing"] input[data-toggle="sample"]');
  ok(box !== null, "the playing status has a Show sample game box");
  page.stream(toggle, true);
  page.click(box);
  await page.release(1);
  const ticked = page.requests.filter((r) => r.path === "/api/toggle");
  equal(ticked.length, 1, "the click asks the model to show the game");
  equal(ticked[0].body.which, "sample", "...for the sample game");
  equal(ticked[0].body.value, true, "...and it was a request to show it");

  /* Now the turns keep coming, and the game appears with the first of them.  The
     clock is left alone throughout: a block being written out is the state worth
     seeing, and a block dropped in finished is not the same claim. */
  const firstAt = turn.findIndex(
    (patch, at) => at > playingAt && patch.op === "insert" && String(patch.id || "").startsWith("sample-")
  );
  ok(firstAt !== -1, "a turn puts the sample block in the feed");
  for (let i = playingAt + 1; i <= firstAt; i += 1) await page.step(0, false);

  const inserted = turn[firstAt];
  const id = inserted.id;
  ok(entry(document, id) !== null, "the sample block is on the page with the first turn on it");
  equal(document.querySelector('[data-status="playing"]').getAttribute("data-state"), "running",
    "and the swarm is still playing while it is there");
  equal(longBlocks(document).length, 1, "one sample block, and it is this one");

  /* The game is written out as it is played, not held back and dropped in whole
     at the end: catch it part way through. */
  await page.type(8);
  const part = typedText(document, id);
  ok(part.length > 20, `the game is being written out while the swarm plays (got ${part.length})`);
  ok(!part.includes("Game over"), "and nothing claims the game is over before it is");

  /* The next turn's update lands on a block that is still being written.  It has
     to carry on from where it got to: starting again from the top is the reader
     watching the same paragraph be written twice. */
  const nextAt = turn.findIndex((patch, at) => at > firstAt && patch.id === id);
  ok(nextAt !== -1, "the next turn updates the same block rather than adding another");
  for (let i = firstAt + 1; i <= nextAt; i += 1) await page.step(0, false);
  const second = typedText(document, id);
  /* No more than one step of the plan may appear at a time: a jump is the page
     showing text the reader never watched being written, because something
     remembered the block as further along than it was. */
  const step = Math.max(
    0,
    ...JSON.parse(
      document.querySelector(`[data-entry-id="${id}"] .typed`).getAttribute("data-typing")
    ).schedule.map(([chars]) => chars)
  );
  ok(second.length - part.length <= step,
    "the page did not skip text to catch up with itself");
  ok(second.startsWith(part),
    "the game carried on from where it was, instead of starting again from the top");
  equal(longBlocks(document).length, 1, "still one block, updated rather than duplicated");

  /* The rest of the run, still with the clock stopped, so the last turn's update
     is caught as it lands rather than after the writing has caught up. */
  for (let i = nextAt + 1; i < turn.length; i += 1) await page.step(0, false);
  ok(typedText(document, id).startsWith("Turn 1"), "every turn kept what was already on the page");
  equal(longBlocks(document).length, 1, "one block, however many turns arrive");

  await page.release(0);
  await page.settle();

  equal(longBlocks(document).length, 1, "one sample block, however many turns arrive");
  equal(document.querySelectorAll(`[data-entry-id="${id}"]`).length, 1,
    "the sample block was replaced, not duplicated");
  equal(typedState(document, id), "2", "the sample game finished typing itself out");
  const text = typedText(document, id);
  ok(text.length > 20, `the sample game is not blank (got ${text.length} characters)`);
  ok(!text.includes("{'"), "a generated engine's moves are shown, not their repr");
  equal(document.querySelectorAll('[data-status="running"]').length, 0,
    "nothing is left running once the run is over");

  page.restore();
}

/* --- 3. a run that will not start, and what the console says about it ------ */

{
  const page = newPage(input.page);
  const document = page.document;

  /* The swarm is down before the first step, which is the answer the server
     gives when it cannot reach Gemini.  Nothing appears in the feed for it, so
     the console is the only place the reason exists. */
  page.refuse(409, JSON.stringify({ error: "gemini-3.1-pro-preview did not answer" }));
  await page.submit(scenarios.prompts.complete);

  const reported = page.console.filter((line) => line.level === "error");
  ok(reported.length > 0, "a run that will not start is written to the console");

  const failure = reported.find((line) => String(line.args[0]).indexOf("/api/turn") !== -1);
  ok(failure !== undefined, "the report names the request that failed");
  if (failure) {
    const note = failure.args[1];
    equal(note.sent.text, scenarios.prompts.complete.length, "the report says how much was sent");
    ok(String(note.why).indexOf("409") !== -1, `and the status the server gave (got ${note.why})`);
    ok(
      String(note.why).indexOf("did not answer") !== -1,
      "and the server's own reason for it, which the status does not carry"
    );
    ok(!JSON.stringify(note).includes(scenarios.prompts.complete.slice(0, 12)),
      "the prompt itself is not in the report, only its length");
  }

  equal(
    page.requests.filter((r) => r.path === "/api/diagnostics").length,
    1,
    "the console then asks the server what it knows"
  );
  const server = page.console.find((line) => line.args[0] === "[hacks] server diagnostics");
  ok(server !== undefined, "and what it knows is printed with the report");
  if (server) {
    equal(server.args[1].key_found, false, "...including that the server has no key");
    equal(server.args[1].key_source, "missing", "...and where it looked for one");
  }
  equal(document.body.getAttribute("data-busy"), "0",
    "and the page is not left busy, waiting for a run that will not start");

  page.restore();
}

/* --- 4. a request that never reaches the server at all --------------------- */

{
  const page = newPage(input.page);

  /* The same page, the same run, and nothing listening: which is a different
     failure with a different fix, so the report has to say which one it was
     rather than only that something went wrong. */
  page.offline();
  await page.submit(scenarios.prompts.complete);

  const reported = page.console.filter(
    (line) => line.level === "error" && String(line.args[0]).indexOf("/api/turn") !== -1
  );
  equal(reported.length, 1, "the request that never left the browser is reported");
  if (reported.length === 1) {
    const note = reported[0].args[1];
    ok(String(note.why).indexOf("never reached the server") !== -1,
      `and it says so (got ${note.why})`);
    ok(String(note.hint).indexOf("diagnostics") !== -1,
      "with something to try, rather than a bare failure");
  }

  page.restore();
}

/* --- 5. an error nobody caught, and a patch nobody could read ---------------- */

{
  const page = newPage(input.page);

  page.fireWindow("unhandledrejection", { reason: new Error("nothing caught this") });
  const report = page.console.find((line) => line.level === "error");
  ok(report !== undefined, "a rejected promise nobody caught is written to the console");
  if (report) {
    ok(String(report.args[1].error).indexOf("nothing caught this") !== -1,
      "...with what it was, which is otherwise invisible");
  }

  page.fireWindow("error", {
    filename: "app.js",
    lineno: 12,
    message: "x is not a function",
    error: new TypeError("x is not a function"),
  });
  const uncaught = page.console.find((line) => line.level === "error" && line.args[1].where);
  ok(uncaught !== undefined, "so is an uncaught error, and where it came from");

  page.restore();
}

if (failures.length) {
  console.error(`\n${failures.length} of ${checks} client checks failed:\n`);
  for (const failure of failures) console.error("  - " + failure + "\n");
  process.exit(1);
}
console.log(`${checks} client checks passed`);
