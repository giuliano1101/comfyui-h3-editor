// Plays a video while enough of it is on screen and `allowed()` says so, and pauses it when it leaves.
function whileVisible(video, allowed) {
  if (!("IntersectionObserver" in window)) { if (allowed()) video.play().catch(() => {}); return; }
  new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (entry.isIntersecting && allowed()) video.play().catch(() => {});
      else if (!entry.isIntersecting && !video.paused) video.pause();
    }
  }, { threshold: 0.35 }).observe(video);
}

// The hero reel: muted until the visitor asks for sound, and still under reduced motion.
(() => {
  const reel = document.getElementById("reel");
  const button = document.getElementById("reel-sound");
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduceMotion) { reel.removeAttribute("autoplay"); reel.pause(); }
  button.addEventListener("click", () => {
    reel.muted = !reel.muted;
    button.setAttribute("aria-pressed", String(!reel.muted));
    if (!reel.muted && reel.paused) reel.play().catch(() => {});
  });
  whileVisible(reel, () => !reduceMotion || !reel.muted);
})();

// The showcase: choosing a render swaps the clip and the words that made it, together.
(() => {
  const SHOTS = {
    "harbor": {
      label: "Midday sun on a fisherman's face", seed: 1248160305, seconds: "3.8 s",
      prompt: "A weathered fisherman in his sixties stands on a harbor dock at midday. Hard sun from straight above lights his sunburnt face, with deep lines, grey stubble, and sweat and salt drying on his skin. He squints toward the sea, then turns his head slowly toward the camera. Fishing boats sit out of focus behind him. Camera: Close-up, handheld, slight drift. Sound: Gulls, water lapping against the dock, a distant boat engine.",
    },
    "golden-hour": {
      label: "A woman in a wheat field at golden hour", seed: 155121007, seconds: "3.8 s",
      prompt: "A young woman with red hair and freckles walks through a wheat field at golden hour. The low sun is behind her and lights the fine hair along her cheek and the edges of her face. She looks back over her shoulder toward the camera and smiles. Camera: Medium close-up, tracking beside her. Sound: Wind through the wheat, birdsong.",
    },
    "neon-rain": {
      label: "A woman in neon light and rain", seed: 308467067, seconds: "3.8 s",
      prompt: "A woman in her thirties stands on a city street at night in the rain. A pink neon sign on one side and a cyan one on the other light her face in two colours, and raindrops run down her cheeks and catch both colours. She blinks the rain away and looks up into it. Camera: Close-up, slow push in. Sound: Steady rain, distant traffic, the hum of neon signs.",
    },
    "candlelight": {
      label: "An old woman by one candle, speaking", seed: 177889935, seconds: "5.2 s",
      prompt: "Tight close-up of an old woman's face in her eighties, filling the frame from forehead to chin. A single candle burns just beside her face, its warm light falling across the deep wrinkles of one cheek and leaving the other side in shadow. Her silver hair is pinned back. She looks up from a letter she holds below the frame and speaks softly, with a small, tired smile. Camera: Tight close-up, locked off, shallow focus. Speaker: The old woman, soft and warm. Dialogue: \"He wrote that he would be home before the snow.\" Sound: A candle flame fluttering, a clock ticking in the room.",
    },
    "boxer": {
      label: "A boxer between rounds under a gym lamp", seed: 606441076, seconds: "3.8 s",
      prompt: "Close-up of a boxer in his late twenties sitting in the corner of a dim gym between rounds, his face and shoulders filling the frame. A single hanging lamp above lights the sweat on his forehead, cheekbones and shoulders. He breathes hard, then lifts his head and looks toward the camera, jaw set. The background is a dark, blurred gym wall. Camera: Close-up, handheld. Sound: Heavy breathing, a muffled crowd, a bell far off.",
    },
  };
  const FACTS = (shot) => [
    ["Model", "HyperFlow H3 Flash"],
    ["LoRA", "Realism Slider V2, strength 1.2"],
    ["Final", "1920 × 1088, " + shot.seconds + ", 24 fps"],
    ["Seed", String(shot.seed)],
  ];
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const video = document.getElementById("show-video");
  const stage = video.parentElement;
  const prompt = document.getElementById("show-prompt");
  const facts = document.getElementById("show-facts");
  const sound = document.getElementById("show-sound");
  const picks = [...document.querySelectorAll(".pick")];

  function words(text) {
    prompt.textContent = "";
    prompt.classList.remove("in");
    text.split(/(\s+)/).forEach((part, i) => {
      if (/^\s+$/.test(part)) { prompt.append(part); return; }
      const word = document.createElement("span");
      word.className = "w";
      word.textContent = part;
      word.style.transitionDelay = Math.min(i * 6, 700) + "ms";
      prompt.append(word);
    });
    requestAnimationFrame(() => requestAnimationFrame(() => prompt.classList.add("in")));
  }

  function choose(pick, focus) {
    const id = pick.dataset.shot;
    const shot = SHOTS[id];
    picks.forEach((each) => {
      const on = each === pick;
      each.setAttribute("aria-selected", String(on));
      each.tabIndex = on ? 0 : -1;
    });
    if (focus) pick.focus();
    facts.textContent = "";
    for (const [name, value] of FACTS(shot)) {
      const dt = document.createElement("dt");
      dt.textContent = name;
      const dd = document.createElement("dd");
      dd.textContent = value;
      facts.append(dt, dd);
    }
    words(shot.prompt);
    const swap = () => {
      video.src = "media/realism/" + id + ".mp4";
      video.poster = "media/realism/" + id + ".jpg";
      video.setAttribute("aria-label", shot.label);
      video.play().catch(() => {});
      stage.classList.remove("swapping");
    };
    if (reduceMotion) swap();
    else { stage.classList.add("swapping"); setTimeout(swap, 220); }
  }

  picks.forEach((pick, i) => {
    pick.addEventListener("click", () => { if (pick.getAttribute("aria-selected") !== "true") choose(pick, false); });
    pick.addEventListener("keydown", (event) => {
      const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
      if (step) { event.preventDefault(); choose(picks[(i + step + picks.length) % picks.length], true); }
    });
  });
  sound.addEventListener("click", () => {
    video.muted = !video.muted;
    sound.setAttribute("aria-pressed", String(!video.muted));
    if (!video.muted && video.paused) video.play().catch(() => {});
  });

  // First words without the reveal, so they are there before the visitor scrolls to them.
  const first = SHOTS.harbor;
  prompt.textContent = first.prompt;
  for (const [name, value] of FACTS(first)) {
    const dt = document.createElement("dt");
    dt.textContent = name;
    const dd = document.createElement("dd");
    dd.textContent = value;
    facts.append(dt, dd);
  }
  whileVisible(video, () => !reduceMotion || !video.muted);
})();

// The launch film starts from the page's own button, with sound; the browser's controls come once it plays.
(() => {
  const film = document.getElementById("film-video");
  const button = document.getElementById("film-play");
  button.addEventListener("click", () => {
    film.controls = true;
    film.parentElement.classList.add("playing");
    film.play().catch(() => {});
    film.focus();
  });
})();

// The cut drives the timeline and the shot pane, the way the editor's playhead does.
(() => {
  const FPS = 24;
  const TOTAL = 360;
  const PROJECT = "The_pass";

  // The four shots of "The pass", with the fields they were rendered from. `cast` is who the shot keeps the same.
  const CLIPS = [
    {
      id: "c822cf892", name: "Wide", from: 0, to: 73, seconds: 3.04, strip: "media/wide.jpg", cast: "Chef",
      action: "@Chef wipes the rim of a white plate at the pass. Behind her, a flame flares up and dies. The pass is long brushed steel under a row of glowing heat lamps; the flame rises tall and orange from a pan on the range, lights the whole kitchen for a moment, then is gone. Steam drifts through the lamp light.",
      camera: "Wide shot at eye level from the dining side of the pass, slow push in",
      speaker: "",
      dialogue: "",
      sound: "The whoosh and roar of a gas flame flaring up, a pan hissing, the low hum of extractor fans. No music, no voices.",
    },
    {
      id: "cc2eecf3a", name: "Detail", from: 73, to: 146, seconds: 3.04, strip: "media/detail.jpg", cast: "",
      action: "Tweezers lower one green leaf onto a seared scallop. The scallop sits on a swipe of golden puree on a white plate under a warm heat lamp; a steady hand holds the steel tweezers, places the small herb leaf and lets go. Steam curls up through the light. The dark kitchen behind is empty and far out of focus.",
      camera: "Macro close-up, locked off, very shallow depth of field",
      speaker: "",
      dialogue: "",
      sound: "A soft metallic clink of tweezers touching porcelain, a pan sizzling quietly in the background, the low hum of extractor fans. No music, no voices.",
    },
    {
      id: "caa4b5e11", name: "Face", from: 146, to: 236, seconds: 3.75, strip: "media/face.jpg", cast: "Chef",
      action: "@Chef studies the plate. Then her eyes lift, and she nods. Only her face is in the frame, from forehead to chin, warm light falling on it from above, the dark kitchen a soft blur behind her. Her eyes are lowered, completely focused; then she raises her eyes to look straight ahead and gives one small, satisfied nod.",
      camera: "Extreme close-up of her face, forehead to chin filling the whole frame, locked off, 85mm lens, very shallow depth of field",
      speaker: "",
      dialogue: "",
      sound: "The low hum of extractor fans and a distant pan hissing. No music, no voices.",
    },
    {
      id: "c23560c00", name: "Bell", from: 236, to: 360, seconds: 5.17, strip: "media/bell.jpg", cast: "Chef",
      action: "@Chef slides the plate across the pass and rings the bell. A small dome-shaped brass call bell sits on the brushed steel in the foreground, in sharp focus. From the right, her hand pushes a white plate with a seared scallop on golden puree, topped with one green herb leaf, across the steel toward the camera until it stops beside the bell. After a beat her palm comes down and taps the button on top of the bell once.",
      camera: "Low angle along the surface of the steel pass under the heat lamps, the bell and the plate sharp in the foreground, the kitchen dark and soft behind",
      speaker: "",
      dialogue: "",
      sound: "Porcelain sliding across steel, a short silence, then one clear bright ding of a brass service bell that rings out over the low hum of extractor fans. No music, no voices.",
    },
  ];

  // Loudest sample in each of 240 slices of the cut's sound, 0 to 100.
  const PEAKS = [17,14,10,13,14,13,12,11,10,9,21,46,66,91,90,48,52,38,29,24,18,18,23,64,19,15,14,16,11,13,13,15,9,11,12,10,13,13,16,19,12,12,11,14,13,10,11,12,12,4,5,5,5,5,7,5,7,9,4,7,7,7,5,6,6,10,5,5,5,6,7,7,6,7,7,7,6,9,6,6,8,5,5,6,8,6,5,7,7,7,6,6,8,6,7,5,6,13,11,12,9,10,12,12,12,13,14,17,12,13,12,12,10,13,13,14,14,16,16,14,14,12,15,17,17,16,14,16,17,20,14,14,15,14,19,18,18,14,17,18,15,12,15,14,13,15,12,16,13,15,17,14,14,11,11,17,22,2,3,3,3,4,2,3,3,3,2,2,3,5,7,6,5,6,6,6,6,6,5,7,6,7,8,8,6,6,5,7,7,9,7,6,7,8,7,7,6,5,6,5,7,6,6,6,7,7,6,7,7,6,6,10,12,6,4,4,3,3,3,3,3,3,3,3,3,3,3,3,63,100,72,56,36,33,38,22,22,21,19,20];

  const $ = (id) => document.getElementById(id);
  const video = $("cut");
  const track = $("track");
  const playhead = $("playhead");
  const clock = $("clock");
  const announce = $("announce");
  const pad = (n) => String(n).padStart(2, "0");
  const timecode = (frame) => "00:" + pad(Math.floor(frame / FPS)) + ":" + pad(frame % FPS);
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* Timeline */

  const ruler = $("ruler");
  for (let second = 0; second * FPS < TOTAL; second++) {
    const tick = document.createElement("span");
    tick.className = "tick";
    tick.style.left = (second * FPS / TOTAL) * 100 + "%";
    tick.textContent = "0:" + pad(second);
    ruler.append(tick);
  }

  const blocks = CLIPS.map((clip) => {
    const block = document.createElement("div");
    block.className = "block";
    block.style.left = (clip.from / TOTAL) * 100 + "%";
    block.style.width = ((clip.to - clip.from) / TOTAL) * 100 + "%";
    block.style.backgroundImage = "url(" + clip.strip + ")";
    const label = document.createElement("span");
    label.textContent = clip.name;
    block.append(label);
    $("picture").append(block);
    return block;
  });

  const wave = $("wave");
  const svgNS = "http://www.w3.org/2000/svg";
  PEAKS.forEach((peak, i) => {
    const height = Math.max(1.2, Math.sqrt(peak / 100) * 40);
    const bar = document.createElementNS(svgNS, "rect");
    bar.setAttribute("x", i + 0.15);
    bar.setAttribute("width", 0.7);
    bar.setAttribute("y", (40 - height) / 2);
    bar.setAttribute("height", height);
    wave.append(bar);
  });

  /* The shot pane */

  const fieldIds = ["action", "camera", "speaker", "dialogue", "sound"];
  const empty = { speaker: "No one", dialogue: "Nothing", sound: "Nothing" };

  function opFor(clip) {
    const fields = { name: clip.name };
    if (clip.cast) fields.cast = ["$" + clip.cast.toLowerCase()];
    fields.action = clip.action;
    fields.camera = clip.camera;
    if (clip.speaker) fields.speaker = clip.speaker;
    if (clip.dialogue) fields.dialogue = clip.dialogue;
    fields.sound = clip.sound;
    fields.seconds = clip.seconds;
    return fields;
  }

  // One block per line, so a line that wraps hangs under its own text instead of the prompt.
  function line(code, parts) {
    const row = document.createElement("span");
    row.className = "line";
    for (const [cls, text] of parts) {
      const span = document.createElement("span");
      if (cls) span.className = cls;
      span.textContent = text;
      row.append(span);
    }
    code.append(row);
  }

  function renderAgent(clip) {
    const code = $("term");
    code.textContent = "";
    line(code, [["p", "$ "], ["", "python h3.py ops " + PROJECT + " shots.json"]]);
    line(code, [["p", "$ "], ["", "python h3.py render " + PROJECT + " "], ["nowrap", clip.id + " --final"]]);
    line(code, [["p", "$ "], ["", "python h3.py export " + PROJECT]]);
    const file = $("file");
    file.textContent = "";
    line(file, [["", "{\"op\": \"add_clip\", \"fields\": {"]]);
    const fields = Object.entries(opFor(clip));
    fields.forEach(([key, value], i) => {
      const last = i === fields.length - 1;
      const shown = typeof value === "number" ? [["n", String(value)]] : [["s", JSON.stringify(value).replace(/","/g, '", "')]];
      line(file, [["", "  "], ["k", JSON.stringify(key) + ": "], ...shown, ["", last ? "}}" : ","]]);
    });
    $("file-name").textContent = clip.name;
  }

  function renderHand(clip) {
    $("shot-name").textContent = clip.name;
    $("shot-meta").textContent = "Final, " + (Math.round(clip.seconds * 10) / 10) + " s";
    for (const id of fieldIds) {
      const cell = $("f-" + id);
      cell.textContent = clip[id] || empty[id];
      cell.classList.toggle("empty", !clip[id]);
      if (id === "action") cell.scrollTop = 0;
    }
    // Who the shot keeps the same: the cast member's picture and name, as in the editor.
    $("cast-who").hidden = !clip.cast;
    $("cast-none").hidden = !!clip.cast;
    // A shot without a speaker shows no empty speech fields.
    $("speech").hidden = !clip.speaker && !clip.dialogue;
  }

  let shown = -1;
  function showClip(index) {
    if (index === shown) return;
    shown = index;
    const clip = CLIPS[index];
    renderHand(clip);
    renderAgent(clip);
    blocks.forEach((block, i) => block.classList.toggle("on", i === index));
    if (!reduceMotion) {
      for (const pane of document.querySelectorAll(".pane")) {
        pane.classList.remove("swap");
        void pane.offsetWidth;
        pane.classList.add("swap");
      }
    }
  }

  /* Playhead */

  const frameNow = () => Math.max(0, Math.min(TOTAL - 1, Math.floor(video.currentTime * FPS + 0.001)));
  const clipAt = (frame) => Math.max(0, CLIPS.findIndex((clip) => frame >= clip.from && frame < clip.to));

  function sync() { show(frameNow()); }
  function show(frame) {
    const index = clipAt(frame);
    playhead.style.left = (frame / TOTAL) * 100 + "%";
    clock.textContent = timecode(frame);
    track.setAttribute("aria-valuenow", frame);
    track.setAttribute("aria-valuetext", timecode(frame) + ", " + CLIPS[index].name);
    showClip(index);
  }

  let ticking = false;
  function tick() {
    sync();
    if (!video.paused) requestAnimationFrame(tick);
    else ticking = false;
  }
  function startTicking() {
    if (!ticking) { ticking = true; requestAnimationFrame(tick); }
  }

  function seek(frame) {
    const clamped = Math.max(0, Math.min(TOTAL - 1, Math.round(frame)));
    video.currentTime = (clamped + 0.5) / FPS;
    sync();
  }

  function frameFromPointer(event) {
    const box = track.getBoundingClientRect();
    if (!box.width) return frameNow();
    return ((event.clientX - box.left) / box.width) * TOTAL;
  }
  let dragging = false;
  // A touch that turns into a vertical scroll is cancelled by the browser, so touches seek on release or drag.
  track.addEventListener("pointerdown", (event) => {
    dragging = true;
    track.setPointerCapture(event.pointerId);
    if (event.pointerType !== "touch") seek(frameFromPointer(event));
  });
  track.addEventListener("pointermove", (event) => { if (dragging) seek(frameFromPointer(event)); });
  track.addEventListener("pointerup", (event) => {
    if (dragging && event.pointerType === "touch") seek(frameFromPointer(event));
    dragging = false;
  });
  track.addEventListener("pointercancel", () => { dragging = false; });
  track.addEventListener("keydown", (event) => {
    const step = event.shiftKey ? FPS : 1;
    const here = frameNow();
    const moves = {
      ArrowLeft: here - step, ArrowRight: here + step, ArrowDown: here - step, ArrowUp: here + step,
      Home: 0, End: TOTAL - 1, PageUp: here + FPS, PageDown: here - FPS,
    };
    if (event.key in moves) { event.preventDefault(); seek(moves[event.key]); }
    else if (event.key === " ") { event.preventDefault(); togglePlay(); }
  });

  /* Transport */

  const room = document.querySelector(".stage");
  const play = $("play");
  function showPlaying() {
    room.classList.toggle("paused", video.paused);
    play.setAttribute("aria-label", video.paused ? "Play" : "Pause");
  }
  function togglePlay() {
    if (video.paused) video.play().catch(() => {});
    else video.pause();
  }
  play.addEventListener("click", togglePlay);
  video.addEventListener("click", togglePlay);
  $("start").addEventListener("click", () => seek(0));
  $("prev").addEventListener("click", () => seek(frameNow() - 1));
  $("next").addEventListener("click", () => seek(frameNow() + 1));
  video.addEventListener("play", () => { showPlaying(); startTicking(); });
  video.addEventListener("pause", () => { showPlaying(); sync(); });
  video.addEventListener("seeked", sync);
  video.addEventListener("timeupdate", sync); // keeps up where animation frames are paused

  const sound = $("sound");
  sound.addEventListener("click", () => {
    video.muted = !video.muted;
    sound.setAttribute("aria-pressed", String(!video.muted));
    if (!video.muted && video.paused) video.play().catch(() => {});
  });

  // The cut opens on the flame at its tallest, the poster's frame, so poster, playhead and panes agree
  // before the video loads and whenever the browser refuses to play it on its own. It plays only while
  // the editor is on screen, and stays paused once the visitor pauses it.
  const START = 36;
  let heldByVisitor = false;
  play.addEventListener("click", () => { heldByVisitor = video.paused; });
  function begin() { video.currentTime = (START + 0.5) / FPS; }
  if (video.readyState >= 1) begin();
  else video.addEventListener("loadedmetadata", begin, { once: true });
  showPlaying();
  show(START);
  whileVisible(video, () => !reduceMotion && !heldByVisitor);

  /* The two ways to make the shot */

  const tabs = [$("tab-hand"), $("tab-agent")];
  function choose(tab, focus) {
    for (const each of tabs) {
      const on = each === tab;
      each.setAttribute("aria-selected", String(on));
      each.tabIndex = on ? 0 : -1;
      $(each.getAttribute("aria-controls")).hidden = !on;
    }
    if (focus) tab.focus();
  }
  tabs.forEach((tab, i) => {
    tab.addEventListener("click", () => choose(tab, false));
    tab.addEventListener("keydown", (event) => {
      if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
        event.preventDefault();
        choose(tabs[(i + 1) % tabs.length], true);
      }
    });
  });

  /* Copy buttons */

  for (const button of document.querySelectorAll("[data-copy]")) {
    const label = button.querySelector("span");
    button.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(button.dataset.copy);
        label.textContent = "Copied";
        button.classList.add("done");
        announce.textContent = "Copied to the clipboard";
      } catch (_) {
        label.textContent = "Select and copy";
      }
      setTimeout(() => {
        label.textContent = "Copy";
        button.classList.remove("done");
        announce.textContent = "";
      }, 1800);
    });
  }
})();
