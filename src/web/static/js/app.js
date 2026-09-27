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

  var nodes = []; /* feed order, mirrors the model */
  var byId = Object.create(null);
  var busy = false;
  var typing = null;

  /* ------------------------------------------------------------------ util */

  function closestEntry(node) {
    while (node && node !== feed) {
      if (node.getAttribute && node.getAttribute("data-entry-id")) return node;
      node = node.parentNode;
    }
    return null;
  }

  function entryById(id) {
    return byId[id] || document.querySelector('[data-entry-id="' + id + '"]');
  }

  function indexOfNode(node) {
    for (var i = 0; i < nodes.length; i += 1) if (nodes[i] === node) return i;
    return -1;
  }

  function register(node) {
    var id = node.getAttribute("data-entry-id");
    if (!id) return;
    byId[id] = node;
    if (nodes.indexOf(node) === -1) nodes.push(node);
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
      var at = nodes[patch.index] || null;
      feed.insertBefore(added, at);
      nodes.splice(patch.index, 0, added);
      register(added);
      afterInsert(added);
    } else if (patch.op === "update") {
      var current = entryById(patch.id);
      var fresh = parse(patch.html);
      if (!fresh) return;
      if (current && current.parentNode) {
        current.parentNode.replaceChild(fresh, current);
        var at = indexOfNode(current);
        if (at !== -1) nodes[at] = fresh;
        else nodes.push(fresh);
        byId[patch.id] = fresh;
        /* A re-rendered subtree is a new one as far as the browser is concerned:
           anything inside it that types itself out or makes a sound has to be
           armed again, or it is left half finished - or invisible - forever. */
        afterInsert(fresh, false);
      } else {
        feed.appendChild(fresh);
        nodes.push(fresh);
        byId[patch.id] = fresh;
        afterInsert(fresh);
      }
    } else if (patch.op === "remove") {
      var doomed = entryById(patch.id);
      if (doomed && doomed.parentNode) doomed.parentNode.removeChild(doomed);
      delete byId[patch.id];
      var at2 = indexOfNode(doomed);
      if (at2 !== -1) nodes.splice(at2, 1);
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
     never hang: each step's own delay is used, and the chunk grows. */
  function startTyping(code) {
    if (!code || code.dataset.typed) return;
    var plan;
    try {
      plan = JSON.parse(code.getAttribute("data-typing") || "{}");
    } catch (err) {
      plan = null;
    }
    var full = code.getAttribute("data-full");
    if (full === null) full = code.textContent;
    code.setAttribute("data-full", full);
    if (!plan || !plan.schedule || !full) {
      code.dataset.typed = "2";
      return;
    }
    if (typing && typing.node) finishTyping(typing.node);
    code.dataset.typed = "1";
    code.textContent = "";
    var at = 0;
    var written = 0;
    typing = { node: code, timer: null };

    function step() {
      if (!typing || typing.node !== code) return;
      var chunk = plan.schedule[at];
      if (!chunk) {
        finishTyping(code);
        return;
      }
      at += 1;
      written += chunk[0];
      code.textContent = full.slice(0, written);
      scrollTyped(code);
      // The next step waits as long as it asked to, not as long as the first.
      typing.timer = window.setTimeout(step, chunk[1]);
    }

    typing.timer = window.setTimeout(step, plan.schedule[0][1]);
  }

  function finishTyping(code) {
    if (typing && typing.node === code && typing.timer) {
      window.clearTimeout(typing.timer);
      typing = null;
    }
    var full = code.getAttribute("data-full");
    if (full !== null) code.textContent = full;
    code.dataset.typed = "2";
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

  /* --------------------------------------------------------------- stream */

  function stream(path, payload) {
    busy = true;
    body.setAttribute("data-busy", "1");
    fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {})
    })
      .then(function (response) {
        if (!response.ok || !response.body) throw new Error("stream failed");
        return readFrames(response.body.getReader());
      })
      .catch(function () {
        body.setAttribute("data-busy", "0");
        busy = false;
      });
  }

  /* Frames are one JSON patch per "data:" line; the server sends them as the
     backend produces them, so the feed fills in while the agents work. */
  function readFrames(reader) {
    var buffer = "";
    var decoder = new TextDecoder();

    function pump() {
      return reader.read().then(function (result) {
        if (result.done) {
          flush();
          body.setAttribute("data-busy", "0");
          busy = false;
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
          /* a malformed frame should not stop the rest of the feed */
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
      });
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

  Array.prototype.forEach.call(feed.children, register);
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
