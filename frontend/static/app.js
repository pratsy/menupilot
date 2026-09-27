const chat = document.getElementById("chat");
const composer = document.getElementById("composer");
const input = document.getElementById("input");
const sendBtn = document.getElementById("send");

const sessionId = (() => {
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
    stepsEl: wrap.querySelector(".steps"),
    bubbleEl: wrap.querySelector(".bubble"),
  };
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

async function sendMessage(message) {
  addUserMessage(message);
  const { stepsEl, bubbleEl } = addAssistantMessage();
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
    bubbleEl.textContent = `Something went wrong: ${err}`;
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
