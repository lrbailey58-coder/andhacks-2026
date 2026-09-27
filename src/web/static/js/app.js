/*
  The browser side of the front end.

  Everything about *what* the feed contains lives in ui.py; this file binds
  behaviour to the markup: Enter (with and without modifiers) submits, streamed
  patches are applied in place, long blocks type themselves out on the schedule
  the model computed, checkboxes ask the model to show or hide a block, dropped
  files land in the editor, and a completed task pings.
*/
(function () {
  "use strict";

  var MODIFIERS = ["ctrlKey", "altKey", "shiftKey", "metaKey"];

  var feed = document.querySelector("[data-feed-inner]");
  var body = document.body;

  var busy = false;
  var typing = null;

  /* How much of each long block is on the page, by entry id.  The blocks are
     re-rendered as their content grows, and a re-render replaces the element, so
     this is the one place that remembers where the writing got to.  Without it
     every new turn would throw away the transcript and write it again. */
  var written = Object.create(null);

  /* Only one block is written at a time - a page that types three paragraphs at
     once is unreadable - so a block that is interrupted goes on this list and is
     carried on when the pen is free.  The key rather than the element, because
     the element is replaced by the next update and the text to carry on from is
     in ``written`` anyway. */
  var interrupted = [];

  /* ------------------------------------------------------------------ util */

  function closestEntry(node) {
    while (node && node !== feed) {
      if (node.getAttribute && node.getAttribute("data-entry-id")) return node;
      node = node.parentNode;
    }
    return null;
  }

  /* The document is the feed.  There is no second list of entries kept beside
     it, because a second list is a second thing to fall out of step with the
     first: nested entries (a question's own editor) are not feed children, and
     the moment one of them was treated as if it were, every position after it
     was off by one and a status that had never arrived was re-created at the
     bottom of the page.  An entry id is unique, so asking the document is
     enough. */
  function entryById(id) {
    if (!id) return null;
    return document.querySelector('[data-entry-id="' + id + '"]');
  }

  /* The child a patch goes in front of, from the index the model sent.  Past
     the end there is nothing to go in front of, which is the end of the feed. */
  function childAt(index) {
    return feed.children[index] || null;
  }

  /* The document scrolls, so the document is what "at the bottom" is a question
     about.  The page element is only a wrapper: it is as tall as its content, so
     measuring it would say the feed is always at the bottom, and every block
     would drag the page down with it. */
  function atBottom() {
    if (!body) return true;
    return body.scrollHeight - body.scrollTop - body.clientHeight < 120;
  }

  function scrollTo(node) {
    if (!node || !node.scrollIntoView) return;
    node.scrollIntoView({ behavior: "smooth", block: "end" });
  }

  /* --------------------------------------------------------- prompt editor */

  /* "if the user presses enter with a modifier, any modifier, it's treated like
     a regular enter keypress inside the editor" (ui.enter_keypress). */
  function enterBehaviour(event) {
    for (var i = 0; i < MODIFIERS.length; i += 1) {
      if (event[MODIFIERS[i]]) return "newline";
    }
    return "submit";
  }

  function autoGrow(textarea) {
    textarea.style.height = "auto";
    textarea.style.height = Math.min(textarea.scrollHeight, 320) + "px";
  }

  function editorText(form) {
    var textarea = form.querySelector(".editor__input");
    return textarea ? textarea.value : "";
  }

  /* What the user asked for while they were still writing, so the run knows
     before anything is generated.  Named as the server expects. */
  function editorToggles(form) {
    var payload = {};
    var boxes = form.querySelectorAll('input[type="checkbox"][data-toggle]');
    for (var i = 0; i < boxes.length; i += 1) {
      var which = boxes[i].getAttribute("data-toggle");
      payload["show_" + which] = boxes[i].checked;
    }
    return payload;
  }

  function submitEditor(form) {
    if (busy) return;
    var text = editorText(form);
    if (!text.trim()) {
      form.animate(
        [
          { transform: "translateX(0)" },
          { transform: "translateX(-4px)" },
          { transform: "translateX(4px)" },
          { transform: "translateX(0)" }
        ],
        { duration: 220, easing: "ease-in-out" }
      );
      return;
    }
    if (document.activeElement && document.activeElement.blur) {
      document.activeElement.blur();
    }
    var payload = editorToggles(form);
    payload.text = text;
    stream("/api/turn", payload);
  }

  /* -------------------------------------------------------------- patches */

  function applyPatch(patch) {
    if (patch.op === "insert") {
      var added = parse(patch.html);
      if (!added) return;
      feed.insertBefore(added, childAt(patch.index));
      afterInsert(added);
    } else if (patch.op === "update") {
      var current = entryById(patch.id);
      var fresh = parse(patch.html);
      if (!fresh) return;
      if (current && current.parentNode) {
        /* In place, wherever it is - a feed entry or something inside one.  The
           element's position in the feed is its own business; nothing here has
           to know whether it is a child of the feed or of a question. */
        current.parentNode.replaceChild(fresh, current);
        afterInsert(fresh, false);
      }
      /* An update for something that is not on the page is dropped rather than
         appended: a fresh copy at the bottom of the feed is a second copy of
         something the reader already has, and it stays there for good. */
    } else if (patch.op === "remove") {
      var doomed = entryById(patch.id);
      if (doomed && doomed.parentNode) doomed.parentNode.removeChild(doomed);
      delete written[patch.id];
    } else if (patch.op === "focus") {
      var target = entryById(patch.id);
      var area = target && target.querySelector(".editor__input");
      if (area) {
        area.focus();
        area.setSelectionRange(area.value.length, area.value.length);
      }
    }
  }

  function parse(html) {
    var holder = document.createElement("template");
    holder.innerHTML = html;
    return holder.content.firstElementChild;
  }

  function afterInsert(node, mayScroll) {
    var area =
      node.matches(".editor:not(.is-submitted)")
        ? node.querySelector(".editor__input")
        : null;
    /* An editor that grows to fit starts at the height of its text, which for a
       fresh question is the single line it is given. */
    if (area && area.getAttribute("data-auto-grow") === "1") autoGrow(area);
    var typed = node.querySelectorAll(".typed");
    for (var i = 0; i < typed.length; i += 1) startTyping(typed[i]);
    var word = node.matches(".multiword") ? node : node.querySelector(".multiword");
    if (word) startWordCycle(word);
    var pinger = node.matches("[data-ping]") ? node : node.querySelector("[data-ping]");
    if (pinger) ping(pinger);
    /* A re-render is not new content arriving, so it does not pull the page
       down: the entry may be a long way up the feed. */
    if (mayScroll !== false && atBottom()) scrollTo(node);
  }

  /* ---------------------------------------------------------------- typing */

  /* Plays back the [chars, delayMs] schedule the model built, so long blocks
     never hang: each step's own delay is used, and the chunk grows.

     A block that arrives with ``data-typed="0"`` has not been written yet and
     must be.  The guard is on "already finished" and not on "has an attribute",
     because the stylesheet hides ``[data-typed="0"]`` and a block whose typing
     never started is a blank one. */
  function startTyping(code) {
    if (!code || code.dataset.typed === "2") return;
    var plan;
    try {
      plan = JSON.parse(code.getAttribute("data-typing") || "{}");
    } catch (err) {
      plan = null;
    }
    var full = code.getAttribute("data-full");
    if (full === null) full = code.textContent;
    code.setAttribute("data-full", full);
    var key = code.getAttribute("data-block");
    if (!plan || !plan.schedule || !plan.schedule.length || !full) {
      /* Nothing to type it out with, so the whole of it is on the page at once -
         and it is remembered as written, so a later block that does have a plan
         does not start again from the top. */
      written[key] = full || "";
      code.dataset.typed = "2";
      return;
    }
    if (typing && typing.node) {
      var interruptedKey = typing.node.getAttribute("data-block");
      finishTyping(typing.node, false);
      if (interrupted.indexOf(interruptedKey) === -1) interrupted.push(interruptedKey);
    }
    code.dataset.typed = "1";

    /* Resume where the writing got to, so a block that grows (the sample game,
       turn by turn) only has to write what is new.  What is remembered is the
       text, not a count of it, because the text can be rewritten as well as
       grown: a block that starts again from the top is the reader watching the
       same paragraph be written twice.  The plan is replayed from the start to
       find the step that carries the text already on the page. */
    var seen = written[key] || "";
    var keep = 0;
    while (keep < full.length && keep < seen.length && full[keep] === seen[keep]) keep += 1;
    var at = 0;
    var done = 0;
    /* At least the first step is written before anything is scheduled: a block
       that waits for its first timer with nothing on the page is a blank block
       for the length of that timer. */
    var want = keep > 0 ? keep : 1;
    while (done < want && at < plan.schedule.length) {
      done += plan.schedule[at][0];
      at += 1;
    }
    written[key] = full.slice(0, done);
    code.textContent = full.slice(0, done);
    if (done >= full.length || at >= plan.schedule.length) {
      finishTyping(code, true);
      return;
    }
    typing = { node: code, timer: null };

    function step() {
      if (!typing || typing.node !== code) return;
      var chunk = plan.schedule[at];
      if (!chunk) {
        finishTyping(code, true);
        return;
      }
      at += 1;
      done += chunk[0];
      written[key] = full.slice(0, done);
      code.textContent = full.slice(0, done);
      scrollTyped(code);
      // The next step waits as long as it asked to, not as long as the first.
      typing.timer = window.setTimeout(step, chunk[1]);
    }

    typing.timer = window.setTimeout(step, plan.schedule[at][1]);
  }

  function finishTyping(code, complete) {
    if (typing && typing.node === code && typing.timer) {
      window.clearTimeout(typing.timer);
      typing = null;
    }
    var full = code.getAttribute("data-full");
    var key = code.getAttribute("data-block");
    if (complete || full === null) {
      if (full !== null) code.textContent = full;
      written[key] = full || "";
      code.dataset.typed = "2";
      resumeInterrupted();
      return;
    }
    /* Another block has the pen now, and this one was not finished.  What has
       been written is what is on the page - not the whole of the text it was
       given, which would let the next update skip the part the reader never saw
       typed.  It stays unfinished, and it is carried on when the pen is free. */
    written[key] = code.textContent || "";
    code.dataset.typed = "1";
  }

  /* Picks up where the last block left off, on the element that is on the page
     now: the one that was interrupted has usually been replaced since. */
  function resumeInterrupted() {
    while (interrupted.length) {
      var node = feed.querySelector('[data-block="' + interrupted.shift() + '"]');
      if (node && node.dataset.typed !== "2") {
        startTyping(node);
        return;
      }
    }
  }

  /* A long block is a window onto itself, so it scrolls its own text as it is
     written.  The page only follows if the reader is already at the bottom of
     the feed: yanking it down on every keystroke is what makes a tall block
     arrive where the reader is not looking. */
  function scrollTyped(node) {
    var window_ = node.parentNode;
    if (window_ && window_.scrollHeight > window_.clientHeight + 1) {
      window_.scrollTop = window_.scrollHeight;
    }
    if (atBottom()) scrollTo(node);
  }

  /* ------------------------------------------------------------- multiword */

  /* The word is swapped where the stylesheet's cycle is invisible, and the
     cycle is restarted in the same breath, so the two cannot drift apart:
     that is what used to change the word in the middle of a scroll. */
  function startWordCycle(node) {
    if (node.getAttribute("data-multiword") === "0") return;
    if (node.dataset.cycling === "1") return;
    node.dataset.cycling = "1";
    var words = [];
    try {
      words = JSON.parse(node.getAttribute("data-words") || "[]");
    } catch (err) {
      words = [];
    }
    if (words.length < 2) return;
    var slot = node.querySelector(".multiword__word");
    if (!slot) return;
    var period = parseInt(node.getAttribute("data-period") || "2600", 10);
    var index = parseInt(node.getAttribute("data-index") || "0", 10);
    var timer = null;

    function step(by) {
      index = (index + by + words.length) % words.length;
      slot.textContent = words[index];
      node.setAttribute("data-index", String(index));
    }

    /* The stylesheet's cycle, from the top: it starts with the word faded out,
       which is the one moment a change cannot be seen. */
    function restart() {
      slot.style.animation = "none";
      void slot.offsetWidth; /* a reflow, so the animation really does restart */
      slot.style.animation = "";
    }

    function schedule() {
      window.clearTimeout(timer);
      timer = window.setTimeout(advance, period);
    }

    function advance() {
      restart();
      step(1);
      schedule();
    }

    /* Scrolling over the word changes it at once, with no scroll of its own,
       and starts the cycle over, so the next change is a whole period away
       instead of whatever was left of the last one. */
    function jump(event) {
      var travel = event.deltaY + event.deltaX;
      if (Math.abs(travel) < 4) return;
      window.clearTimeout(timer);
      slot.style.animation = "none";
      step(travel < 0 ? -1 : 1);
      schedule();
    }

    schedule();
    node.addEventListener("wheel", jump, { passive: true });
  }

  /* ------------------------------------------------------------------ ping */

  /* The sound is the file the app ships with, played through one element that
     is rewound rather than rebuilt, so overlapping pings still land. */
  var sound = null;

  function ping(node) {
    var src = node && node.getAttribute("data-audio");
    if (!src) return;
    try {
      if (!sound) {
        sound = new Audio(src);
        sound.preload = "auto";
        sound.volume = 0.5;
      }
      sound.currentTime = 0;
      var played = sound.play();
      /* A tab that has never been clicked blocks playback, and that is not
         worth breaking the feed over. */
      if (played && played.catch) {
        played.catch(function () {
          /* nothing to do */
        });
      }
    } catch (err) {
      /* no sound is better than a broken feed */
    }
  }

  /* ---------------------------------------------------------- copy, fetch */

  function textOf(entry) {
    if (!entry) return "";
    var sent = entry.querySelector(".editor__sent");
    if (sent) return sent.textContent.trim();
    var code = entry.querySelector(".typed");
    if (code) {
      return code.getAttribute("data-full") || code.textContent;
    }
    var area = entry.querySelector(".editor__input");
    return area ? area.value : "";
  }

  function copy(entry) {
    var text = textOf(entry);
    if (!text) return Promise.resolve(false);
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text).then(
        function () { return true; },
        function () { return legacyCopy(text); }
      );
    }
    return Promise.resolve(legacyCopy(text));
  }

  function legacyCopy(text) {
    var area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    var ok = false;
    try { ok = document.execCommand("copy"); } catch (err) { ok = false; }
    document.body.removeChild(area);
    return ok;
  }

  function download(entry, name) {
    var text = textOf(entry);
    if (!text) return;
    var blob = new Blob([text + "\n"], { type: "text/plain;charset=utf-8" });
    var url = URL.createObjectURL(blob);
    var link = document.createElement("a");
    link.href = url;
    link.download = name || "download.txt";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    window.setTimeout(function () { URL.revokeObjectURL(url); }, 0);
  }

  /* --------------------------------------------------------- diagnostics */

  /* A page that has silently stopped is the hardest thing in the world to help
     with over a shoulder: there is nothing on it to read, and nothing in the
     feed that says what went missing.  So everything the page cannot fix by
     itself is written to the console as one object - expandable in place, and
     copyable whole into a chat window. */

  var DIAGNOSTICS_PATH = "/api/diagnostics";

  /* What was sent, as a shape rather than as content.  The prompt can be a whole
     pasted document, and this report ends up in channels; the length is the
     useful half, because "no text and two files" and "a page and a half" are
     different mistakes to go looking for. */
  function shapeOf(payload) {
    if (!payload) return { text: 0, files: 0, extras: [] };
    var extras = [];
    for (var key in payload) {
      if (key === "text" || key === "files") continue;
      extras.push(key + "=" + payload[key]);
    }
    return {
      text: String(payload.text || "").length,
      files: (payload.files || []).length,
      extras: extras
    };
  }

  function report(what, detail) {
    var note = { what: what, when: new Date().toISOString() };
    if (typeof location !== "undefined") note.page = location.href;
    for (var key in detail) {
      if (detail[key] !== undefined && detail[key] !== null) note[key] = detail[key];
    }
    console.error("[hacks] " + what, note);
    return note;
  }

  function print(note) {
    if (note) console.error("[hacks] server diagnostics", note);
  }

  /* The server's own words, which say more than a status code does: "no
     session" and "the swarm is down" are both a 4xx, and only one of them is
     the reader's doing.  Read from a body that was never streamed, so the happy
     path costs nothing. */
  function reasonOf(response) {
    if (typeof response.text !== "function") return Promise.resolve(null);
    return response.text().then(
      function (text) {
        if (!text) return null;
        try {
          var body = JSON.parse(text);
          if (body && body.error) return String(body.error);
        } catch (err) {
          /* not JSON; the raw text is the next best thing */
        }
        return text.slice(0, 300);
      },
      function () {
        return null;
      }
    );
  }

  /* What the server knows: which API is switched on, whether a key was found
     and where from, and how the recent model calls went.  Asked for when
     something goes wrong rather than on every page load, because that is the
     only time it is worth anything. */
  function diagnostics() {
    return fetch(DIAGNOSTICS_PATH)
      .then(function (response) {
        return response.ok ? response.json() : null;
      })
      .catch(function () {
        return null;
      });
  }

  /* --------------------------------------------------------------- stream */

  /* ``quiet`` is for a request made *during* a run - the override, and the
     checkboxes.  They must not claim the page is idle when the run that is
     holding it is still going. */
  function stream(path, payload, quiet) {
    if (!quiet) {
      busy = true;
      body.setAttribute("data-busy", "1");
    }
    function idle() {
      if (quiet) return;
      body.setAttribute("data-busy", "0");
      busy = false;
    }

    /* What the failure turned out to be, worked out as it happens: the server's
       own message when it answered, the browser's own story when the request
       never got that far. */
    var why = null;
    var hint = "";
    var said = false;

    function complain(problem) {
      /* Once.  A stream that fails after reading a few frames rejects through
         the same path, and the reason is still the one already reported. */
      if (said) return;
      said = true;
      report(path + " failed", {
        sent: shapeOf(payload),
        why: why || "the stream stopped part way",
        hint: hint,
        error: String((problem && problem.message) || problem)
      });
      diagnostics().then(print);
    }

    fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {})
    })
      .then(function (response) {
        if (!response.ok) {
          return reasonOf(response).then(function (reason) {
            why = (response.status || "?") + " " + (reason || "no reason given");
            hint =
              response.status === 400
                ? "the server did not recognise the request; reload the page for a fresh session"
                : "";
            throw new Error("the server answered " + response.status);
          });
        }
        if (!response.body) {
          why = "the answer carried no stream";
          throw new Error("no stream in the answer");
        }
        return readFrames(response.body.getReader(), quiet);
      })
      .catch(function (problem) {
        /* A fetch that rejects never reached the server, and the browser says so
           with a TypeError and nothing else.  The cause is never in this file:
           the server is not running, the page was opened from somewhere other
           than the server, a proxy or an extension is in the way, or the
           machine is not on the network it thinks it is. */
        if (!why && problem && problem.name === "TypeError") {
          why = "the request never reached the server";
          hint =
            "is the server running on the host this page came from, and did the " +
            "browser open this page from that same host?  " +
            DIAGNOSTICS_PATH + " will say whether the server has a Gemini key.";
        }
        idle();
        complain(problem);
      });
  }

  /* Frames are one JSON patch per "data:" line; the server sends them as the
     backend produces them, so the feed fills in while the agents work. */
  function readFrames(reader, quiet) {
    var buffer = "";
    var decoder = new TextDecoder();

    function pump() {
      return reader.read().then(function (result) {
        if (result.done) {
          flush();
          if (!quiet) {
            body.setAttribute("data-busy", "0");
            busy = false;
          }
          return;
        }
        buffer += decoder.decode(result.value, { stream: true });
        var parts = buffer.split("\n\n");
        buffer = parts.pop();
        for (var i = 0; i < parts.length; i += 1) handleFrame(parts[i]);
        return pump();
      });
    }

    function flush() {
      if (buffer.trim()) handleFrame(buffer);
      buffer = "";
    }

    function handleFrame(frame) {
      var lines = frame.split("\n");
      for (var i = 0; i < lines.length; i += 1) {
        var line = lines[i];
        if (line.indexOf("data:") !== 0) continue;
        try {
          applyPatch(JSON.parse(line.slice(5).trim()));
        } catch (err) {
          /* A malformed frame should not stop the rest of the feed, but it is
             not supposed to happen either: the server sent something this file
             cannot read, and that is worth a line in the console rather than a
             block that is quietly missing. */
          console.warn("[hacks] a patch from the server could not be read", {
            error: String((err && err.message) || err),
            frame: line.slice(0, 200)
          });
        }
      }
    }

    return pump();
  }

  /* ------------------------------------------------------------ listeners */

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Enter") return;
    var area = event.target;
    if (!area || !area.classList || !area.classList.contains("editor__input")) return;
    if (enterBehaviour(event) === "newline") return; /* plain newline */
    event.preventDefault();
    submitEditor(closestEntry(area));
  });

  document.addEventListener("input", function (event) {
    var area = event.target;
    if (area && area.classList && area.classList.contains("editor__input")) {
      autoGrow(area);
    }
  });

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form.classList || !form.classList.contains("editor")) return;
    event.preventDefault();
    submitEditor(form);
  });

  document.addEventListener("click", function (event) {
    var target = event.target;
    if (!target || !target.closest) return;

    var action = target.closest("[data-action]");
    if (action) {
      var entry = closestEntry(action);
      if (action.getAttribute("data-action") === "copy") {
        copy(entry).then(function (ok) {
          if (ok) {
            action.textContent = "Copied";
            action.classList.add("is-done");
            window.setTimeout(function () {
              action.textContent = "Copy";
              action.classList.remove("is-done");
            }, 1400);
          }
        });
      } else if (action.getAttribute("data-action") === "download") {
        download(entry, action.getAttribute("data-download-name"));
      }
      return;
    }

    var toggle = target.closest("[data-toggle]");
    if (toggle && toggle.tagName === "INPUT") {
      /* A checkbox that is no longer attached to the newest thing in the feed
         is drawn greyed out and does nothing at all. */
      if (toggle.disabled) return;
      /* Only the box that was clicked changes.  The other boxes of the same kind
         are separate questions - a request about the run, or about the answer -
         and answering one of them says nothing about the others. */
      stream("/api/toggle", {
        which: toggle.getAttribute("data-toggle"),
        value: !!toggle.checked,
        context: toggle.getAttribute("data-context") || "run"
      }, true);
      return;
    }

    /* The manual override on <StatusPlaying>.  The playtest is in flight on the
       server, so the turn in progress still has to come back; the button says so
       straight away rather than leaving the reader to wonder whether the click
       landed, and the status settles with a <Result> when the swarm really has
       stopped. */
    var override = target.closest("[data-override]");
    if (override && override.tagName === "BUTTON") {
      if (override.disabled) return;
      override.disabled = true;
      override.textContent = "Stopping the swarm…";
      override.classList.add("is-stopping");
      stream("/api/override", { run: parseInt(override.getAttribute("data-run"), 10) }, true);
    }
  });

  /* "alternatively, drag and drop a file" */
  var dragDepth = 0;
  document.addEventListener("dragenter", function (event) {
    var editor = event.target.closest && event.target.closest(".editor[data-drop-target]");
    if (!editor) return;
    dragDepth += 1;
    editor.classList.add("editor--drop");
  });

  document.addEventListener("dragleave", function () {
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0) {
      var open = document.querySelectorAll(".editor--drop");
      for (var i = 0; i < open.length; i += 1) open[i].classList.remove("editor--drop");
    }
  });

  document.addEventListener("dragover", function (event) {
    if (event.target.closest && event.target.closest(".editor[data-drop-target]")) {
      event.preventDefault();
    }
  });

  document.addEventListener("drop", function (event) {
    var editor = event.target.closest && event.target.closest(".editor[data-drop-target]");
    if (!editor) return;
    event.preventDefault();
    dragDepth = 0;
    editor.classList.remove("editor--drop");
    var files = event.dataTransfer && event.dataTransfer.files;
    if (!files || !files.length) return;
    var area = editor.querySelector(".editor__input");
    var pending = files.length;
    for (var i = 0; i < files.length; i += 1) {
      (function (file) {
        var reader = new FileReader();
        reader.onload = function () {
          var text = String(reader.result || "");
          if (area.value.trim()) area.value = area.value.trim() + "\n\n";
          area.value += "--- FILE: " + file.name + " ---\n" + text;
          autoGrow(area);
          pending -= 1;
          if (pending === 0) area.focus();
        };
        reader.readAsText(file);
      })(files[i]);
    }
  });

  /* ------------------------------------------------------------------ boot */

  /* The last two lines of this file are the ones that get somebody out of a
     dead page.  Anything this file throws, and any promise anywhere on the page
     that is rejected and never caught, is reported here - an uncaught error is
     otherwise invisible in the console unless the person reading it knows to
     look in the right tab.  Kept off the streams above, which report themselves
     with the request that failed. */

  if (typeof window !== "undefined" && window.addEventListener) {
    window.addEventListener("error", function (event) {
      report("uncaught error", {
        where: event.filename ? event.filename + ":" + event.lineno : "somewhere",
        error: String((event.error && event.error.message) || event.message)
      });
    });
    window.addEventListener("unhandledrejection", function (event) {
      var reason = event.reason || {};
      report("a promise was rejected and never caught", {
        error: String((reason && reason.message) || reason)
      });
      diagnostics().then(print);
    });
  }

  /* Typed into the console by hand, for the case where nothing failed but the
     run still will not start:  hacksDiagnostics()  */
  if (typeof window !== "undefined") {
    window.hacksDiagnostics = function () {
      return diagnostics().then(print);
    };
  }

  var blocks = document.querySelectorAll(".typed");
  for (var i = 0; i < blocks.length; i += 1) {
    blocks[i].setAttribute("data-full", blocks[i].textContent);
    blocks[i].dataset.typed = "2"; /* already typed: this is the first paint */
  }
  var words = document.querySelectorAll(".multiword");
  for (var j = 0; j < words.length; j += 1) startWordCycle(words[j]);
  var first = document.querySelector(".editor:not(.is-submitted) .editor__input");
  if (first) {
    if (first.getAttribute("data-auto-grow") === "1") autoGrow(first);
    first.focus();
  }
})();
