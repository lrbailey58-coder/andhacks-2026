/*
  A DOM small enough to read and complete enough to run app.js against.

  The pass 3 notes were about logic - a status that arrived in the wrong place,
  a block that never wrote itself out - and none of that is in the stylesheet, so
  grepping the script for a string cannot catch it.  This is the smallest thing
  that can: a tree, the attributes app.js reads, a selector engine for the
  handful of selector shapes it uses, and an HTML parser for the markup the
  templates produce.  It is deliberately not a browser - there is no layout, no
  styling, and no events bubbling.

  What it does have to be is honest about what it cannot do, so anything app.js
  asks for that is not modelled is a loud error rather than a silent undefined.
*/

const VOID = new Set([
  "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
  "param", "source", "track", "wbr",
]);

const RAW = new Set(["script", "style"]);

const ENTITIES = {
  amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " ",
  mdash: "—", ndash: "–", hellip: "…", middot: "·",
  check: "✓", rarr: "→", larr: "←", uarr: "↑", darr: "↓",
  times: "×", rsquo: "’", lsquo: "‘", ldquo: "“", rdquo: "”",
};

function decode(text) {
  return text.replace(/&(#x?[0-9a-fA-F]+|[a-zA-Z]+);/g, (whole, name) => {
    if (name[0] === "#") {
      const code = name[1] === "x" || name[1] === "X"
        ? parseInt(name.slice(2), 16)
        : parseInt(name.slice(1), 10);
      return Number.isFinite(code) ? String.fromCodePoint(code) : whole;
    }
    return name in ENTITIES ? ENTITIES[name] : whole;
  });
}

/* ------------------------------------------------------------------ text -- */

class Text {
  constructor(data) {
    this.nodeType = 3;
    this.data = data;
    this.parentNode = null;
  }

  get textContent() {
    return this.data;
  }
}

/* --------------------------------------------------------------- elements -- */

class Element {
  constructor(tagName, ownerDocument) {
    this.nodeType = 1;
    this.tagName = tagName.toUpperCase();
    this.ownerDocument = ownerDocument;
    this.attrs = new Map();
    this.childNodes = [];
    this.parentNode = null;
    this.style = {};
    this.listeners = new Map();
    this.value = "";
    this._checked = false;
    this.scrollTop = 0;
    /* No layout, so a block is never taller than its window and never scrolls:
       the only thing that reads these is the "should the page follow?" check,
       and a page that never scrolls is the boring case. */
    this.scrollHeight = 0;
    this.clientHeight = 0;
    this.offsetWidth = 0;
  }

  /* -- attributes -- */

  getAttribute(name) {
    const key = String(name).toLowerCase();
    return this.attrs.has(key) ? this.attrs.get(key) : null;
  }

  setAttribute(name, value) {
    this.attrs.set(String(name).toLowerCase(), String(value));
  }

  removeAttribute(name) {
    this.attrs.delete(String(name).toLowerCase());
  }

  hasAttribute(name) {
    return this.attrs.has(String(name).toLowerCase());
  }

  /* data-typed="1" as .dataset.typed, the way the browser spells it. */
  get dataset() {
    const element = this;
    return new Proxy(
      {},
      {
        get(_target, key) {
          if (typeof key !== "string") return undefined;
          const name = "data-" + key.replace(/[A-Z]/g, (c) => "-" + c.toLowerCase());
          const value = element.getAttribute(name);
          return value === null ? undefined : value;
        },
        set(_target, key, value) {
          const name = "data-" + String(key).replace(/[A-Z]/g, (c) => "-" + c.toLowerCase());
          element.setAttribute(name, value);
          return true;
        },
        has(_target, key) {
          const name = "data-" + String(key).replace(/[A-Z]/g, (c) => "-" + c.toLowerCase());
          return element.hasAttribute(name);
        },
      }
    );
  }

  /* ``disabled`` and ``checked`` are properties on a real element, and app.js
     reads them as properties.  Backed by the attribute so the two cannot
     disagree. */
  get disabled() {
    return this.hasAttribute("disabled");
  }

  set disabled(value) {
    if (value) this.setAttribute("disabled", "");
    else this.removeAttribute("disabled");
  }

  get checked() {
    return this._checked === true;
  }

  set checked(value) {
    this._checked = value === true;
  }

  get classList() {
    const element = this;
    return {
      add(...names) {
        const held = new Set((element.getAttribute("class") || "").split(/\s+/).filter(Boolean));
        names.forEach((name) => held.add(name));
        element.setAttribute("class", [...held].join(" "));
      },
      remove(...names) {
        const held = new Set((element.getAttribute("class") || "").split(/\s+/).filter(Boolean));
        names.forEach((name) => held.delete(name));
        element.setAttribute("class", [...held].join(" "));
      },
      contains(name) {
        return (element.getAttribute("class") || "").split(/\s+/).includes(name);
      },
    };
  }

  get className() {
    return this.getAttribute("class") || "";
  }

  /* -- tree -- */

  get children() {
    return this.childNodes.filter((node) => node.nodeType === 1);
  }

  get childElementCount() {
    return this.children.length;
  }

  get firstElementChild() {
    return this.children[0] || null;
  }

  get firstChild() {
    return this.childNodes[0] || null;
  }

  get nextSibling() {
    if (!this.parentNode) return null;
    const held = this.parentNode.childNodes;
    return held[held.indexOf(this) + 1] || null;
  }

  appendChild(node) {
    return this.insertBefore(node, null);
  }

  insertBefore(node, reference) {
    if (node.parentNode) node.parentNode.removeChild(node);
    node.parentNode = this;
    if (reference === null || reference === undefined) {
      this.childNodes.push(node);
    } else {
      const at = this.childNodes.indexOf(reference);
      if (at === -1) throw new Error("insertBefore: the reference is not a child");
      this.childNodes.splice(at, 0, node);
    }
    return node;
  }

  replaceChild(next, current) {
    const at = this.childNodes.indexOf(current);
    if (at === -1) throw new Error("replaceChild: the node is not a child");
    if (next.parentNode) next.parentNode.removeChild(next);
    this.childNodes[at] = next;
    next.parentNode = this;
    current.parentNode = null;
    return current;
  }

  removeChild(node) {
    const at = this.childNodes.indexOf(node);
    if (at === -1) throw new Error("removeChild: the node is not a child");
    this.childNodes.splice(at, 1);
    node.parentNode = null;
    return node;
  }

  remove() {
    if (this.parentNode) this.parentNode.removeChild(this);
  }

  /* -- text -- */

  get textContent() {
    return this.childNodes.map((node) => node.textContent).join("");
  }

  set textContent(value) {
    for (const node of this.childNodes) node.parentNode = null;
    this.childNodes = [];
    if (value !== "" && value !== null && value !== undefined) {
      this.appendChild(new Text(String(value)));
    }
  }

  get innerHTML() {
    return this.childNodes
      .map((node) => (node.nodeType === 3 ? node.data : node.outerHTML))
      .join("");
  }

  get outerHTML() {
    const held = [...this.attrs]
      .map(([name, value]) => ` ${name}="${String(value).replace(/"/g, "&quot;")}"`)
      .join("");
    if (VOID.has(this.tagName.toLowerCase())) return `<${this.tagName.toLowerCase()}${held}>`;
    return (
      `<${this.tagName.toLowerCase()}${held}>` +
      this.innerHTML +
      `</${this.tagName.toLowerCase()}>`
    );
  }

  set innerHTML(html) {
    for (const node of this.childNodes) node.parentNode = null;
    this.childNodes = [];
    for (const node of parseFragment(html, this.ownerDocument)) this.appendChild(node);
  }

  /* -- selectors -- */

  matches(selector) {
    return selector.split(",").some((one) => matchCompound(this, one.trim()));
  }

  closest(selector) {
    let node = this;
    while (node && node.nodeType === 1) {
      if (node.matches(selector)) return node;
      node = node.parentNode;
    }
    return null;
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }

  querySelectorAll(selector) {
    const found = [];
    for (const node of descendants(this)) {
      if (node !== this && matchChain(node, selector)) found.push(node);
    }
    return found;
  }

  /* -- the handful of behaviours app.js calls -- */

  addEventListener(type, handler) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(handler);
  }

  removeEventListener(type, handler) {
    const held = this.listeners.get(type) || [];
    const at = held.indexOf(handler);
    if (at !== -1) held.splice(at, 1);
  }

  dispatch(type, event) {
    let node = this;
    while (node) {
      for (const handler of [...(node.listeners.get(type) || [])]) handler(event);
      node = node.parentNode;
    }
  }

  focus() {
    this.ownerDocument.activeElement = this;
  }

  blur() {
    if (this.ownerDocument.activeElement === this) this.ownerDocument.activeElement = null;
  }

  select() {}
  setSelectionRange() {}
  scrollIntoView() {}
  animate() {}
  play() {
    return Promise.resolve();
  }
}

function* descendants(node) {
  for (const child of node.childNodes) {
    if (child.nodeType !== 1) continue;
    yield child;
    yield* descendants(child);
  }
}

/* --------------------------------------------------------------- selectors -- */

/* One compound selector: an optional tag, any number of .classes and [attrs],
   and any number of :not(...) negations.  Chains ("a b") match a descendant. */

const COMPOUND = /^(?<tag>[a-zA-Z][\w-]*)?(?<rest>(?:[.#][\w-]+|\[[^\]]*\]|:not\([^)]*\))*)$/;

function parseCompound(source) {
  const match = COMPOUND.exec(source);
  if (!match) throw new Error(`dom: cannot parse the selector "${source}"`);
  const part = { tag: match.groups.tag ? match.groups.tag.toLowerCase() : null, classes: [], attrs: [], not: [] };
  const rest = match.groups.rest || "";
  const token = /\.([\w-]+)|\[([^\]]*)\]|:not\(([^)]*)\)/g;
  let piece;
  while ((piece = token.exec(rest)) !== null) {
    if (piece[1]) part.classes.push(piece[1]);
    else if (piece[2] !== undefined) {
      const eq = piece[2].indexOf("=");
      if (eq === -1) part.attrs.push([piece[2].trim(), null]);
      else {
        const name = piece[2].slice(0, eq).trim();
        let value = piece[2].slice(eq + 1).trim();
        if ((value[0] === '"' && value.endsWith('"')) || (value[0] === "'" && value.endsWith("'"))) {
          value = value.slice(1, -1);
        }
        part.attrs.push([name, value]);
      }
    } else part.not.push(piece[3].trim());
  }
  return part;
}

function matchCompound(node, source) {
  const part = typeof source === "string" ? parseCompound(source) : source;
  if (node.nodeType !== 1) return false;
  if (part.tag && node.tagName.toLowerCase() !== part.tag) return false;
  for (const name of part.classes) {
    if (!node.classList.contains(name)) return false;
  }
  for (const [name, value] of part.attrs) {
    if (!node.hasAttribute(name)) return false;
    if (value !== null && node.getAttribute(name) !== value) return false;
  }
  for (const negated of part.not) {
    if (matchCompound(node, negated)) return false;
  }
  return true;
}

/* Split on the descendant combinator, but not inside brackets or parens. */
function splitChain(selector) {
  const parts = [];
  let depth = 0;
  let current = "";
  for (const character of selector.trim()) {
    if (character === "[" || character === "(") depth += 1;
    if (character === "]" || character === ")") depth -= 1;
    if (/\s/.test(character) && depth === 0) {
      if (current) parts.push(current);
      current = "";
      continue;
    }
    current += character;
  }
  if (current) parts.push(current);
  return parts.map(parseCompound);
}

function matchChain(node, selector) {
  const chain = splitChain(selector);
  if (!matchCompound(node, chain[chain.length - 1])) return false;
  let wanted = chain.length - 1;
  let walk = node.parentNode;
  while (walk && wanted > 0) {
    if (matchCompound(walk, chain[wanted - 1])) wanted -= 1;
    walk = walk.parentNode;
  }
  return wanted === 0;
}

/* -------------------------------------------------------------- html parse -- */

const TAG = /<(\/?)([a-zA-Z][\w:-]*)((?:\s+[^\s=/>]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+))?)*)\s*(\/?)>/g;
const ATTR = /([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g;

function parseAttributes(source, element) {
  let match;
  ATTR.lastIndex = 0;
  while ((match = ATTR.exec(source)) !== null) {
    const name = match[1];
    const value = match[2] ?? match[3] ?? match[4] ?? "";
    element.setAttribute(name, decode(value));
  }
}

function parseFragment(html, document) {
  const roots = [];
  const stack = [];
  const push = (node) => {
    if (stack.length) stack[stack.length - 1].appendChild(node);
    else roots.push(node);
  };

  let at = 0;
  TAG.lastIndex = 0;
  let match;
  while ((match = TAG.exec(html)) !== null) {
    if (match.index > at) {
      const text = html.slice(at, match.index);
      if (text.trim() || stack.length) push(new Text(decode(text)));
      else if (text) push(new Text(text));
    }
    at = TAG.lastIndex;
    const [, closing, name, attrs, selfClosing] = match;
    const lower = name.toLowerCase();

    if (closing) {
      /* Close up to the matching open tag, so a stray close does not throw the
         tree out: the templates do not produce them and a harness that crashes
         tells you nothing about app.js. */
      for (let i = stack.length - 1; i >= 0; i -= 1) {
        if (stack[i].tagName.toLowerCase() === lower) {
          stack.length = i;
          break;
        }
      }
      continue;
    }

    const element = new Element(lower, document);
    parseAttributes(attrs, element);
    push(element);
    if (selfClosing || VOID.has(lower)) continue;
    if (RAW.has(lower)) {
      const close = html.indexOf("</" + lower, at);
      element.textContent = html.slice(at, close === -1 ? html.length : close);
      at = close === -1 ? html.length : html.indexOf(">", close) + 1;
      TAG.lastIndex = at;
      continue;
    }
    stack.push(element);
  }
  if (at < html.length) {
    const text = html.slice(at);
    if (text.trim() || stack.length) push(new Text(decode(text)));
  }
  return roots;
}

/* ---------------------------------------------------------------- document -- */

class Document extends Element {
  constructor() {
    super("#document", null);
    this.nodeType = 9;
    this.ownerDocument = this;
    this.activeElement = null;
    this.documentElement = new Element("html", this);
    this.body = new Element("body", this);
    this.documentElement.appendChild(this.body);
    this.appendChild(this.documentElement);
  }

  createElement(tagName) {
    const element = new Element(tagName, this);
    if (String(tagName).toLowerCase() === "template") {
      element.content = new Element("#fragment", this);
      /* ``holder.innerHTML = html`` then ``holder.content.firstElementChild``:
         the template's children are its content, not its own. */
      Object.defineProperty(element, "innerHTML", {
        get: () => element.content.innerHTML,
        set: (html) => {
          element.content.innerHTML = html;
        },
      });
    }
    return element;
  }

  execCommand() {
    return true;
  }
}

export function createDocument(bodyHtml = "") {
  const document = new Document();
  document.body.innerHTML = bodyHtml;
  return document;
}

export { Element, Text, decode, parseFragment, matchCompound, matchChain };
