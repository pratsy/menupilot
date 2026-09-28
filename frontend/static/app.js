const chat = document.getElementById("chat");
const composer = document.getElementById("composer");
const input = document.getElementById("input");
const sendBtn = document.getElementById("send");

let sessionId = (() => {
  let id = localStorage.getItem("eff_session_id");
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem("eff_session_id", id);
  }
  return id;
})();

function addUserMessage(text) {
  const wrap = document.createElement("div");
  wrap.className = "msg user";
  wrap.innerHTML = `<div class="bubble"></div>`;
  wrap.querySelector(".bubble").textContent = text;
  chat.appendChild(wrap);
  scrollToBottom();
}

function addAssistantMessage() {
  const wrap = document.createElement("div");
  wrap.className = "msg assistant";
  wrap.innerHTML = `<div class="steps"></div><div class="bubble" hidden></div>`;
  chat.appendChild(wrap);
  scrollToBottom();
  return {
    wrapEl: wrap,
    stepsEl: wrap.querySelector(".steps"),
    bubbleEl: wrap.querySelector(".bubble"),
  };
}

// The entire conversation is replayed to the model on every turn, so an old
// exclusion or city can otherwise bleed into a search that has nothing to do with
// it. Rather than guess when a message is "really" a new search, offer an explicit,
// honest reset: a new session id starts the backend's history clean, no fuzzy
// detection involved. Shown only after an actual set of recommendations (a table or
// a restaurant heading), not after a plain clarifying question.
function looksLikeCompletedSearch(markdown) {
  return /\|\s*Dish\s*\|/i.test(markdown || "") || /^###\s/m.test(markdown || "");
}

function addNewSearchPrompt(wrapEl) {
  const row = document.createElement("div");
  row.className = "next-move";
  row.innerHTML = `<span>Keep going on this, or</span> <button type="button" class="new-search">🧭 start a new search</button>`;
  row.querySelector("button").addEventListener("click", startNewSearch);
  wrapEl.appendChild(row);
}

function startNewSearch() {
  sessionId = crypto.randomUUID();
  localStorage.setItem("eff_session_id", sessionId);
  chat.innerHTML = "";
  showGreeting();
  input.focus();
}

// Cities with a verified, ready-to-go cache right now - kept in sync by hand with
// what's actually been seeded (see backend/scripts/seed_report.json). Anywhere else
// still works, it just means a live scout instead of an instant one.
const READY_DESTINATIONS = [
  { city: "Barcelona", flavor: "tapas country" },
  { city: "Rome", flavor: "pasta and piazzas" },
  { city: "Berlin", flavor: "currywurst and canals" },
];

// A greeting the platform opens with, so the chat never starts on a blank page
// waiting for the traveler to guess what to say. Purely a frontend touch - it
// never reaches the backend or the model, which asks the same things on its own
// once a real message arrives; this just makes the first impression proactive.
function showGreeting() {
  const { bubbleEl } = addAssistantMessage();
  bubbleEl.hidden = false;
  bubbleEl.innerHTML = marked.parse(
    "Wherever you've landed, I've got you. Tell me the **city** and what you're **craving** " +
    "— vegetarian, vegan, allergic to something, just picky — and I'll scout out the real places, " +
    "not the tourist traps.\n\n" +
    "Passport's fully stamped for **Barcelona, Rome, and Berlin** — those get the full scout, " +
    "instantly. Name anywhere else in Europe and I'll still go looking, just live, on the spot."
  );
  addDestinationChips();
}

function addDestinationChips() {
  const wrap = document.createElement("div");
  wrap.className = "destinations";
  wrap.setAttribute("role", "group");
  wrap.setAttribute("aria-label", "Ready-to-go destinations");

  READY_DESTINATIONS.forEach(({ city, flavor }) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "stamp";
    chip.innerHTML = `<span class="stamp-city">${escapeHtml(city)}</span><span class="stamp-flavor">${escapeHtml(flavor)}</span>`;
    chip.addEventListener("click", () => {
      wrap.remove();
      sendMessage(`I've just landed in ${city} — what's good to eat?`);
    });
    wrap.appendChild(chip);
  });

  chat.appendChild(wrap);
  scrollToBottom();
}

function scrollToBottom() {
  chat.scrollTop = chat.scrollHeight;
}

function addStep(stepsEl, label) {
  const line = document.createElement("div");
  line.className = "step-line pending";
  line.innerHTML = `<span>${escapeHtml(label)}</span>`;
  stepsEl.appendChild(line);
  scrollToBottom();
  return line;
}

function resolveStep(line, resultLabel) {
  line.classList.remove("pending");
  const span = document.createElement("span");
  span.className = "result";
  span.textContent = `— ${resultLabel}`;
  line.appendChild(span);
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

function openLinksInNewWindow(container) {
  container.querySelectorAll("a[href]").forEach((a) => {
    a.target = "_blank";
    a.rel = "noopener noreferrer";
  });
}

async function sendMessage(message) {
  addUserMessage(message);
  const { wrapEl, stepsEl, bubbleEl } = addAssistantMessage();
  let pendingStepLine = null;

  sendBtn.disabled = true;
  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, message }),
    });

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let sepIndex;
      while ((sepIndex = buffer.indexOf("\n\n")) !== -1) {
        const rawEvent = buffer.slice(0, sepIndex);
        buffer = buffer.slice(sepIndex + 2);
        handleEvent(rawEvent);
      }
    }
  } catch (err) {
    bubbleEl.hidden = false;
    bubbleEl.textContent = `Hit a snag on the road: ${err}`;
  } finally {
    sendBtn.disabled = false;
  }

  function handleEvent(rawEvent) {
    const lines = rawEvent.split("\n");
    let eventType = "message";
    let dataStr = "";
    for (const line of lines) {
      if (line.startsWith("event:")) eventType = line.slice(6).trim();
      if (line.startsWith("data:")) dataStr += line.slice(5).trim();
    }
    if (eventType === "done") return;
    if (!dataStr) return;

    let payload;
    try {
      payload = JSON.parse(dataStr);
    } catch {
      return;
    }

    if (payload.type === "step") {
      pendingStepLine = addStep(stepsEl, payload.label);
    } else if (payload.type === "step_result") {
      if (pendingStepLine) resolveStep(pendingStepLine, payload.label);
      pendingStepLine = null;
    } else if (payload.type === "message") {
      bubbleEl.hidden = false;
      bubbleEl.innerHTML = marked.parse(payload.content || "");
      openLinksInNewWindow(bubbleEl);
      if (looksLikeCompletedSearch(payload.content)) addNewSearchPrompt(wrapEl);
      scrollToBottom();
    } else if (payload.type === "error") {
      bubbleEl.hidden = false;
      bubbleEl.textContent = `Error: ${payload.message}`;
    }
  }
}

composer.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  input.style.height = "auto";
  sendMessage(text);
});

input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    composer.requestSubmit();
  }
});

input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 140) + "px";
});

showGreeting();
