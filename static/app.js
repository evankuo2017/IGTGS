const state = {
  engine: null,
  selectedVideo: null,
  selectedFile: null,
  searchResults: [],
  analysis: null,
  accidentalPreference: undefined,
  loadingTimer: null,
  chordDb: null,
  activeProgressionIndex: 0,
  currentVisualIndex: -1,
  chordPositions: {},
};

const FIXED_BEAT_DETECTOR = "madmom";
const FIXED_CHORD_DETECTOR = "chord-cnn-lstm";

const LOCAL_CHORD_DB_URL = "/static/vendor/chords/guitar.json";
const LOCAL_CHORD_IMAGE_BASE = "/static/chord-diagrams";

const loadingMessages = [
  "正在下載或接收音訊...",
  "正在執行 Beat Detection...",
  "正在執行 Chord Recognition...",
  "正在整理 Beat & Chord Map...",
];

const dom = {
  engineChip: document.querySelector("#engine-chip"),
  beatDetector: document.querySelector("#beat-detector"),
  chordDetector: document.querySelector("#chord-detector"),
  searchForm: document.querySelector("#search-form"),
  searchInput: document.querySelector("#search-input"),
  searchFeedback: document.querySelector("#search-feedback"),
  searchResults: document.querySelector("#search-results"),
  audioFile: document.querySelector("#audio-file"),
  fileMeta: document.querySelector("#file-meta"),
  selectedTitle: document.querySelector("#selected-title"),
  selectedSubtitle: document.querySelector("#selected-subtitle"),
  analyzeButton: document.querySelector("#analyze-button"),
  statusPanel: document.querySelector("#status-panel"),
  statusMessage: document.querySelector("#status-message"),
  resultsPanel: document.querySelector("#results-panel"),
  resultTitle: document.querySelector("#result-title"),
  resultSubtitle: document.querySelector("#result-subtitle"),
  summaryChips: document.querySelector("#summary-chips"),
  analysisSummary: document.querySelector("#analysis-summary"),
  analysisAudio: document.querySelector("#analysis-audio"),
  playbackStatus: document.querySelector("#playback-status"),
  chordGrid: document.querySelector("#chord-grid"),
  guitarChords: document.querySelector("#guitar-chords"),
  rawOutput: document.querySelector("#raw-output"),
  tabs: document.querySelectorAll(".tab-button"),
  panels: document.querySelectorAll(".tab-panel"),
};

const enharmonicMap = {
  Db: "Csharp",
  "C#": "Csharp",
  "D♭": "Csharp",
  "C♯": "Csharp",
  Eb: "Eb",
  "D#": "Eb",
  "E♭": "Eb",
  "D♯": "Eb",
  Gb: "Fsharp",
  "F#": "Fsharp",
  "G♭": "Fsharp",
  "F♯": "Fsharp",
  Ab: "Ab",
  "G#": "Ab",
  "A♭": "Ab",
  "G♯": "Ab",
  Bb: "Bb",
  "A#": "Bb",
  "B♭": "Bb",
  "A♯": "Bb",
};

const suffixMap = {
  "": "major",
  maj: "major",
  M: "major",
  m: "minor",
  min: "minor",
  "-": "minor",
  dim: "dim",
  dim7: "dim7",
  aug: "aug",
  "+": "aug",
  sus2: "sus2",
  sus4: "sus4",
  sus: "sus4",
  "7": "7",
  maj7: "maj7",
  M7: "maj7",
  m7: "m7",
  min7: "m7",
  "6": "6",
  m6: "m6",
  "9": "9",
  add9: "add9",
  major: "major",
  minor: "minor",
  dominant7: "7",
  minor7: "m7",
};

function setFeedback(message, isError) {
  dom.searchFeedback.textContent = message || "";
  dom.searchFeedback.classList.toggle("error", Boolean(isError));
}

function formatBytes(bytes) {
  if (!bytes) return "";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

function formatSeconds(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) {
    return "--:--";
  }
  const rounded = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(rounded / 60);
  const remain = rounded % 60;
  return `${minutes}:${String(remain).padStart(2, "0")}`;
}

function escapeHtml(value) {
  return String(value === null || value === undefined ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function normalizeAccidentals(value) {
  return (value || "").replace(/♯/g, "#").replace(/♭/g, "b");
}

function accidentalToUnicode(value) {
  return (value || "")
    .replace(/##/g, "𝄪")
    .replace(/bb/g, "𝄫")
    .replace(/#/g, "♯")
    .replace(/b/g, "♭");
}

function isMinorQuality(quality) {
  return quality === "min" || quality === "minor" || quality === "m"
    || (quality.startsWith("min") && quality !== "maj" && quality !== "minor")
    || (quality.startsWith("m") && !quality.startsWith("maj"));
}

function computeAccidentalPreference(chords) {
  if (!Array.isArray(chords) || !chords.length) {
    return undefined;
  }

  let sharpCount = 0;
  let flatCount = 0;
  const noteTokenRegex = /[A-G](?:#|b)/g;

  chords.forEach((chord) => {
    if (!chord) {
      return;
    }
    const tokens = normalizeAccidentals(chord).match(noteTokenRegex);
    if (!tokens) {
      return;
    }
    tokens.forEach((token) => {
      if (token.includes("#")) sharpCount += 1;
      if (token.includes("b")) flatCount += 1;
    });
  });

  if (sharpCount > flatCount) return "sharp";
  if (flatCount > sharpCount) return "flat";
  return undefined;
}

function getAnalysisAccidentalPreference(analysis) {
  if (!analysis) {
    return undefined;
  }
  const sourceChords = analysis.chordGridData && Array.isArray(analysis.chordGridData.chords)
    ? analysis.chordGridData.chords
    : analysis.uniqueChords;
  return computeAccidentalPreference(sourceChords);
}

function stripHtml(value) {
  return String(value || "").replace(/<[^>]+>/g, "");
}

const quarterRestSymbolCache = new Map();

function getQuarterRestSymbol() {
  if (!quarterRestSymbolCache.has("light")) {
    quarterRestSymbolCache.set(
      "light",
      `<span style="display:inline-flex;align-items:center;justify-content:center;width:1.5em;height:1.5em;" class="chord-rest-symbol quarter-rest-responsive">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 125" style="width:100%;height:100%;">
          <path d="M64.803,74.67c-0.98-1.278-7.545-9.942-7.901-10.349c-13.58-17.867-7.955-15.804,4.359-30.901c0,0-19.013-26.224-19.694-27.125c-0.681-0.901-0.86-1.063-1.348-0.708c-0.488,0.354,0.029-0.042-0.689,0.5c-0.718,0.542-0.59,0.445,0,1.25c15.479,21.868,0.753,31.257-3.728,35.5c5.457,6.805,14.635,18.344,17.25,21.708c-17.729-7.792-28.104,16.146-2.542,30.042c1.458-0.667,0,0,1.458-0.667C42.261,84.691,39.846,67.136,64.803,74.67z"/>
        </svg>
      </span>`,
    );
  }
  return quarterRestSymbolCache.get("light");
}

const ENHARMONIC_EQUIVALENTS = {
  C: ["C", "B#", "Dbb"],
  "C#": ["C#", "Db", "B##"],
  D: ["D", "C##", "Ebb"],
  "D#": ["D#", "Eb", "Fbb"],
  E: ["E", "D##", "Fb"],
  F: ["F", "E#", "Gbb"],
  "F#": ["F#", "Gb", "E##"],
  G: ["G", "F##", "Abb"],
  "G#": ["G#", "Ab"],
  A: ["A", "G##", "Bbb"],
  "A#": ["A#", "Bb", "Cbb"],
  B: ["B", "A##", "Cb"],
};

function getEnharmonicSpelling(note, keyContext) {
  const normalizedNote = normalizeAccidentals(note);
  const equivalents = Object.values(ENHARMONIC_EQUIVALENTS).find((group) =>
    group.some((variant) => variant === normalizedNote),
  );
  if (!equivalents) {
    return note;
  }

  const keyUsesFlats = keyContext.includes("b") || keyContext.includes("♭");
  const keyUseSharps = keyContext.includes("#") || keyContext.includes("♯");
  const naturalVariant = equivalents.find((variant) => !variant.includes("#") && !variant.includes("b"));
  if (naturalVariant) {
    return naturalVariant;
  }
  if (keyUsesFlats) {
    const flatVariant = equivalents.find((variant) => variant.includes("b") && !variant.includes("bb"));
    if (flatVariant) {
      return accidentalToUnicode(flatVariant);
    }
  }
  if (keyUseSharps) {
    const sharpVariant = equivalents.find((variant) => variant.includes("#") && !variant.includes("##"));
    if (sharpVariant) {
      return accidentalToUnicode(sharpVariant);
    }
  }
  return accidentalToUnicode(equivalents[0]);
}

function translateScaleDegreeInversion(root, quality, inversion) {
  const notes = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
  const notesWithFlats = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"];
  const normalizedRoot = normalizeAccidentals(root);
  const usesFlats = normalizedRoot.includes("b");
  const noteArray = usesFlats ? notesWithFlats : notes;
  const rootIndex = noteArray.indexOf(normalizedRoot);
  if (rootIndex === -1) {
    return inversion;
  }

  let scaleDegree = inversion;
  let accidental = 0;
  if (inversion.startsWith("b")) {
    accidental = -1;
    scaleDegree = inversion.substring(1);
  } else if (inversion.startsWith("#")) {
    accidental = 1;
    scaleDegree = inversion.substring(1);
  }

  const degree = parseInt(scaleDegree, 10);
  if (Number.isNaN(degree)) {
    return inversion;
  }

  const intervals = isMinorQuality(quality)
    ? [0, 2, 3, 5, 7, 8, 10, 12, 14, 15, 17, 19, 20, 22]
    : [0, 2, 4, 5, 7, 9, 11, 12, 14, 16, 17, 19, 21, 23];
  const semitones = (intervals[degree - 1] || 0) + accidental;
  return noteArray[(rootIndex + semitones) % 12] || inversion;
}

function getBassNoteFromInversion(root, quality, inversion, accidentalPreference) {
  const notes = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
  const notesWithFlats = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"];
  const normalizedRoot = normalizeAccidentals(root);
  const enharmonicRootMap = { Fb: "E", Cb: "B", "E#": "F", "B#": "C" };
  const calculationRoot = enharmonicRootMap[normalizedRoot] || normalizedRoot;
  const usesFlats = normalizedRoot.includes("b");
  const isFlatInversion = inversion.startsWith("b");
  const preferSharps = accidentalPreference === "sharp";
  const preferFlats = accidentalPreference === "flat";
  const primaryNoteArray = preferFlats ? notesWithFlats : preferSharps ? notes : (usesFlats ? notesWithFlats : notes);
  const rootIndex = primaryNoteArray.indexOf(calculationRoot);
  if (rootIndex === -1) {
    return inversion;
  }

  const minor = isMinorQuality(quality);
  let inversionNumber = inversion;
  let intervalAdjustment = 0;

  if (inversion.startsWith("b")) {
    inversionNumber = inversion.substring(1);
    if (!(minor && inversionNumber === "3")) {
      intervalAdjustment = -1;
    }
  } else if (inversion.startsWith("#")) {
    inversionNumber = inversion.substring(1);
    intervalAdjustment = 1;
  }

  const degree = parseInt(inversionNumber, 10);
  if (Number.isNaN(degree)) {
    return inversion;
  }

  let bassSemitones = 0;
  if (degree === 2) bassSemitones = 2;
  else if (degree === 3) bassSemitones = minor ? 3 : 4;
  else if (degree === 4) bassSemitones = 5;
  else if (degree === 5) bassSemitones = 7;
  else if (degree === 6) bassSemitones = 9;
  else if (degree === 7) bassSemitones = 11;
  else if (degree === 9) bassSemitones = 14;
  else return inversion;

  bassSemitones += intervalAdjustment;
  let result = primaryNoteArray[(rootIndex + bassSemitones) % 12] || inversion;

  if (root.includes("#") || root.includes("♯")) {
    if (result === "C" && inversion === "3" && root.startsWith("G#")) {
      result = "B#";
    } else if (result === "F" && inversion === "3" && root.startsWith("C#")) {
      result = "E#";
    } else if (result === "G" && inversion === "3" && root.startsWith("D#")) {
      result = "F##";
    }
    if (root.startsWith("G#") && minor && inversion === "3") {
      result = "B";
    }
    if (root.startsWith("G#") && minor && inversion === "7") {
      result = "F##";
    }
  } else if (root.includes("b") || root.includes("♭")) {
    if (root.startsWith("Fb") && inversion === "3") result = "Ab";
    else if (root.startsWith("Fb") && inversion === "5") result = "Cb";
    else if (root.startsWith("Cb") && inversion === "3") result = "Eb";
    else if (root.startsWith("Cb") && inversion === "5") result = "Gb";
    else if (result.includes("#")) {
      const enharmonicMap = { "C#": "Db", "D#": "Eb", "F#": "Gb", "G#": "Ab", "A#": "Bb" };
      result = enharmonicMap[result] || result;
    }
  } else {
    result = getEnharmonicSpelling(result, root);
  }

  if (isFlatInversion && !root.includes("#") && !root.includes("♯")) {
    if (result === "D#" || result === "D♯") result = "Eb";
    else if (result === "A#" || result === "A♯") result = "Bb";
    else if (result === "G#" || result === "G♯") result = "Ab";
    else if (result === "C#" || result === "C♯") result = "Db";
    else if (result === "F#" || result === "F♯") result = "Gb";
  }

  return accidentalToUnicode(result);
}

function resolveBassPart(root, quality, bassPart, accidentalPreference) {
  if (!bassPart) {
    return "";
  }
  const normalizedBass = normalizeAccidentals(bassPart);
  if (!/^[b#]?\d+$/.test(normalizedBass)) {
    return normalizedBass;
  }

  const commonInversions = new Set([
    "2", "3", "4", "5", "6", "7", "9",
    "b2", "b3", "b4", "b5", "b6", "b7", "b9",
    "#2", "#3", "#4", "#5", "#6", "#7", "#9",
  ]);

  return commonInversions.has(normalizedBass)
    ? getBassNoteFromInversion(root, quality, normalizedBass, accidentalPreference)
    : translateScaleDegreeInversion(root, quality, normalizedBass);
}

function formatChordDisplay(chordName, accidentalPreference) {
  if (!chordName) return "";

  if (["N", "N/C", "N.C.", "NC"].includes(chordName)) {
    return getQuarterRestSymbol();
  }

  const parts = chordName.split("/");
  const mainPart = parts[0] || "";
  const bassPart = parts[1] || "";

  let root = "";
  let quality = "";
  if (mainPart.includes(":")) {
    const mainSections = mainPart.split(":");
    root = mainSections[0] || "";
    quality = mainSections.slice(1).join(":");
  } else {
    const rootMatch = mainPart.match(/^([A-G][#b]?)(.*)$/);
    if (rootMatch) {
      root = rootMatch[1] || "";
      quality = rootMatch[2] || "";
    } else {
      root = mainPart;
    }
  }

  if (quality === "min" || quality === "minor") {
    quality = "m";
  } else if (quality.startsWith("min") && quality.length > 3 && quality !== "minor") {
    quality = `m${quality.substring(3)}`;
  }

  if (accidentalPreference) {
    if (accidentalPreference === "flat" && root.includes("#")) {
      const sharpToFlat = { "C#": "Db", "D#": "Eb", "F#": "Gb", "G#": "Ab", "A#": "Bb" };
      root = sharpToFlat[root] || root;
    } else if (accidentalPreference === "sharp" && root.includes("b")) {
      const flatToSharp = { Db: "C#", Eb: "D#", Gb: "F#", Ab: "G#", Bb: "A#" };
      root = flatToSharp[root] || root;
    }
  }

  const resolvedBass = resolveBassPart(root, quality, bassPart, accidentalPreference);
  root = accidentalToUnicode(root);

  if (quality.includes("°")) {
    quality = quality.replace(/dim/g, "");
    quality = quality.replace(/°/g, '<span style="font-weight: 400; position:relative;top:-1px">°</span>');
  }

  const formattedRoot = `<span style="font-weight: 400;">${root}</span>`;

  if (quality === "maj") {
    quality = "";
  } else if (quality === "min" || quality === "minor") {
    quality = '<span style="font-weight: 400;">m</span>';
  } else if (quality.includes("sus")) {
    if (quality.includes("sus") && quality.includes("(")) {
      quality = quality.replace(/sus(\d+)(?=\()/g, '<span style="font-weight: 400;">sus</span><sup style="font-weight: 300; font-size: 0.7em; line-height: 1;">$1</sup>');
      quality = quality.replace(/sus(?=\()/g, '<span style="font-weight: 400;">sus</span>');
    } else {
      quality = quality.replace(/sus(\d+)/g, '<span style="font-weight: 400;">sus</span><sup style="font-weight: 300; font-size: 0.7em; line-height: 1;">$1</sup>');
    }
  } else if (quality === "m7b5" || quality === "min7b5" || quality.includes("half-dim") || quality.includes("halfdim")) {
    quality = '<span style="font-weight: 400; position:relative;top:-1px">ø</span><sup style="font-weight: 300; font-size: 0.7em; line-height: 1; vertical-align: super;">7</sup>';
  } else if (quality.includes("dim") && !quality.includes("°")) {
    quality = quality.replace("dim", '<span style="font-weight: 400; position:relative;top:-1px">°</span>');
  } else if (quality.includes("aug")) {
    quality = quality.replace("aug", '<span style="font-weight: 400; position:relative">+</span>');
  } else if (quality.includes("7") || quality.includes("9") || quality.includes("11") || quality.includes("13")) {
    if (quality.startsWith("min")) {
      quality = '<span style="font-weight: 400;">m</span>' + quality.substring(3).replace(/(\d+)/g, '<sup style="font-weight: 300; font-size: 0.7em; line-height: 1; vertical-align: super;">$1</sup>');
    } else if (quality.startsWith("maj")) {
      if (quality === "maj7") {
        quality = '<span style="font-weight: 400; position:relative;top:-1px">Δ</span><sup style="font-weight: 300; font-size: 0.7em; line-height: 1; vertical-align: super;">7</sup>';
      } else {
        quality = '<span style="font-weight: 400;">maj</span>' + quality.substring(3).replace(/(\d+)/g, '<sup style="font-weight: 300; font-size: 0.7em; line-height: 1; vertical-align: super;">$1</sup>');
      }
    } else {
      quality = `<span style="font-weight: 400;">${quality.replace(/(\d+)/g, '</span><sup style="font-weight: 300; font-size: 0.7em; line-height: 1; vertical-align: super;">$1</sup><span style="font-weight: 400;">')}</span>`;
      quality = quality.replace(/<span style="font-weight: 400;"><\/span>/g, "");
    }
  } else if (quality) {
    quality = `<span style="font-weight: 400;">${quality}</span>`;
  }

  if (quality.includes("add")) {
    quality = quality.replace(/add(\d+)/g, '<span style="font-weight: 400;">add</span><sup style="font-weight: 300; font-size: 0.7em; line-height: 1; vertical-align: super;">$1</sup>');
  }
  if (quality.includes("(") && quality.includes(")")) {
    quality = quality.replace(/\(b(\d+)\)/g, '<sup style="font-weight: 300; font-size: 0.7em; line-height: 1; vertical-align: super;">⁽♭$1⁾</sup>');
    quality = quality.replace(/\(#(\d+)\)/g, '<sup style="font-weight: 300; font-size: 0.7em; line-height: 1; vertical-align: super;">⁽♯$1⁾</sup>');
    quality = quality.replace(/\((\d+)\)/g, '<sup style="font-weight: 300; font-size: 0.7em; line-height: 1; vertical-align: super;">⁽$1⁾</sup>');
  }
  if (quality.includes("b5") || quality.includes("b7") || quality.includes("b9") || quality.includes("b13")) {
    quality = quality.replace(/b(\d+)/g, '<sup style="font-weight: 300; font-size: 0.7em; line-height: 1; vertical-align: super;">♭$1</sup>');
  }
  if (quality.includes("#5") || quality.includes("#9") || quality.includes("#11")) {
    quality = quality.replace(/#(\d+)/g, '<sup style="font-weight: 300; font-size: 0.7em; line-height: 1; vertical-align: super;">♯$1</sup>');
  }

  let formattedChord = quality ? `${formattedRoot}${quality}` : formattedRoot;
  if (resolvedBass) {
    formattedChord += `<span style="font-weight: 400; margin:0 0.1em">/</span><span style="font-weight: 400;">${accidentalToUnicode(resolvedBass)}</span>`;
  }
  return formattedChord;
}

async function loadEngineStatus() {
  try {
    const response = await fetch("/api/health");
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.error || "分析引擎初始化失敗");
    }

    state.engine = data;
    dom.engineChip.textContent = "IGTGS 本地引擎已就緒";
    dom.engineChip.classList.remove("muted-badge");
  } catch (error) {
    dom.engineChip.textContent = error.message || "分析引擎載入失敗";
    dom.engineChip.classList.add("muted-badge");
  }
}

function updateSelectedSource() {
  if (state.selectedFile) {
    dom.selectedTitle.textContent = state.selectedFile.name;
    dom.selectedSubtitle.textContent = `Upload | ${formatBytes(state.selectedFile.size)}`;
    dom.analyzeButton.disabled = false;
    return;
  }
  if (state.selectedVideo) {
    dom.selectedTitle.textContent = state.selectedVideo.title;
    dom.selectedSubtitle.textContent = `YouTube | ${state.selectedVideo.channel || "Unknown channel"}`;
    dom.analyzeButton.disabled = false;
    return;
  }
  dom.selectedTitle.textContent = "尚未選擇";
  dom.selectedSubtitle.textContent = "請先搜尋並選擇影片，或上傳音檔。";
  dom.analyzeButton.disabled = true;
}

function renderSearchResults() {
  if (!state.searchResults.length) {
    dom.searchResults.innerHTML = "";
    return;
  }

  dom.searchResults.innerHTML = state.searchResults
    .map((result) => {
      const isActive = state.selectedVideo && state.selectedVideo.id === result.id;
      return `
        <article class="search-result">
          <img src="${result.thumbnail}" alt="${escapeHtml(result.title)}" />
          <div>
            <h4>${escapeHtml(result.title)}</h4>
            <div class="result-meta">
              <span>${escapeHtml(result.channel || "Unknown channel")}</span>
              ${result.duration ? `<span>${escapeHtml(result.duration)}</span>` : ""}
              ${result.upload_date ? `<span>${escapeHtml(result.upload_date)}</span>` : ""}
            </div>
          </div>
          <button class="ghost-button ${isActive ? "active" : ""}" data-video-id="${result.id}">
            ${isActive ? "已選擇" : "選擇"}
          </button>
        </article>
      `;
    })
    .join("");

  dom.searchResults.querySelectorAll("[data-video-id]").forEach((button) => {
    button.addEventListener("click", () => {
      const selected = state.searchResults.find((item) => item.id === button.dataset.videoId);
      state.selectedVideo = selected || null;
      state.selectedFile = null;
      dom.audioFile.value = "";
      dom.fileMeta.textContent = "";
      renderSearchResults();
      updateSelectedSource();
    });
  });
}

async function handleSearch(event) {
  event.preventDefault();
  const query = dom.searchInput.value.trim();
  if (!query) {
    setFeedback("請輸入搜尋關鍵字或 YouTube 連結。", true);
    return;
  }

  setFeedback("搜尋中...");
  try {
    const response = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.error || "搜尋失敗");
    }
    state.searchResults = data.results || [];
    if (!state.searchResults.length) {
      setFeedback("找不到結果，請換個關鍵字再試。", true);
      renderSearchResults();
      return;
    }
    setFeedback(`找到 ${state.searchResults.length} 個結果。`);
    renderSearchResults();
  } catch (error) {
    setFeedback(error.message || "搜尋失敗", true);
  }
}

function startLoading() {
  let index = 0;
  dom.statusMessage.textContent = loadingMessages[index];
  dom.statusPanel.classList.remove("hidden");
  state.loadingTimer = window.setInterval(() => {
    index = (index + 1) % loadingMessages.length;
    dom.statusMessage.textContent = loadingMessages[index];
  }, 1500);
}

function stopLoading() {
  if (state.loadingTimer) {
    window.clearInterval(state.loadingTimer);
  }
  state.loadingTimer = null;
  dom.statusPanel.classList.add("hidden");
}

function renderSummaryCards(analysis) {
  const cards = [
    ["Beat Model", analysis.summary.beatModel || "--"],
    ["Chord Model", analysis.summary.chordModel || "--"],
    ["BPM", analysis.summary.bpm || "--"],
    ["Time Sig", `${analysis.summary.timeSignature || 4}/4`],
    ["Measures", analysis.summary.totalMeasures || 0],
    ["Duration", `${analysis.summary.audioDuration || 0}s`],
  ];

  dom.analysisSummary.innerHTML = cards
    .map(
      (entry) => `
        <div class="analysis-card">
          <span class="label">${entry[0]}</span>
          <span class="value">${entry[1]}</span>
        </div>
      `,
    )
    .join("");
}

function renderSummaryChips(analysis) {
  const chips = [
    `Source: ${analysis.sourceType}`,
    `Beat: ${analysis.summary.beatModel || "--"}`,
    `Chord: ${analysis.summary.chordModel || "--"}`,
    `Time: ${analysis.summary.timeSignature || 4}/4`,
  ];
  dom.summaryChips.innerHTML = chips.map((chip) => `<span class="summary-chip">${chip}</span>`).join("");
}

function getChordProgression(analysis) {
  const synchronized = analysis.analysisResult && analysis.analysisResult.synchronizedChords
    ? analysis.analysisResult.synchronizedChords
    : [];
  const beats = analysis.analysisResult && analysis.analysisResult.beats
    ? analysis.analysisResult.beats
    : [];

  const progression = [];
  let previousChord = "";
  synchronized.forEach((item) => {
    const chord = item.chord || "";
    if (!chord || chord === previousChord || ["N", "N/C", "N.C.", "NC"].includes(chord)) {
      previousChord = chord || previousChord;
      return;
    }
    progression.push({
      chord,
      beatIndex: item.beatIndex,
      beatNum: item.beatNum,
      time: beats[item.beatIndex] ? beats[item.beatIndex].time : null,
    });
    previousChord = chord;
  });
  return progression;
}

function getCurrentVisualIndexForTime(analysis, currentTime) {
  const mapping = analysis.chordGridData && analysis.chordGridData.originalAudioMapping
    ? analysis.chordGridData.originalAudioMapping
    : [];
  if (!mapping.length) return -1;

  let activeIndex = mapping[0].visualIndex;
  for (let index = 0; index < mapping.length; index += 1) {
    if (currentTime >= mapping[index].timestamp) {
      activeIndex = mapping[index].visualIndex;
    } else {
      break;
    }
  }
  return activeIndex;
}

function getCurrentChordName(analysis, visualIndex) {
  if (!analysis.chordGridData || !analysis.chordGridData.chords) return "";
  return analysis.chordGridData.chords[visualIndex] || "";
}

function shouldDisplayChordAtIndex(analysis, cell) {
  if (!analysis.chordGridData || !analysis.chordGridData.chords) {
    return false;
  }
  if (cell.isShift || cell.isPadding || !cell.chord || ["N", "N/C", "N.C.", "NC"].includes(cell.chord)) {
    return false;
  }

  const chords = analysis.chordGridData.chords;
  const previousChord = cell.index > 0 ? chords[cell.index - 1] : "";
  return previousChord !== cell.chord;
}

function syncPlaybackState(analysis) {
  if (!dom.analysisAudio) return;

  const currentTime = dom.analysisAudio.currentTime || 0;
  state.currentVisualIndex = getCurrentVisualIndexForTime(analysis, currentTime);
  const currentChord = getCurrentChordName(analysis, state.currentVisualIndex);

  if (currentChord) {
    const progression = getChordProgression(analysis);
    const matchedIndex = progression.findIndex((item) => item.chord === currentChord);
    if (matchedIndex >= 0) {
      state.activeProgressionIndex = matchedIndex;
    }
  }

  renderChordGrid(analysis);
  void renderGuitarChords(analysis);

  if (dom.playbackStatus) {
    const currentLabel = formatSeconds(currentTime);
    const chordLabel = currentChord
      ? `目前和弦：${stripHtml(formatChordDisplay(currentChord, state.accidentalPreference))}`
      : "目前尚未進入和弦區段";
    dom.playbackStatus.textContent = `${currentLabel} · ${chordLabel}`;
  }
}

function setupAudioPlayer(analysis) {
  if (!dom.analysisAudio) return;

  const audio = dom.analysisAudio;
  audio.pause();
  audio.currentTime = 0;
  state.currentVisualIndex = -1;

  if (!analysis.playbackUrl) {
    audio.removeAttribute("src");
    audio.load();
    if (dom.playbackStatus) {
      dom.playbackStatus.textContent = "目前來源沒有可播放音訊";
    }
    return;
  }

  audio.src = analysis.playbackUrl;
  audio.load();
  audio.ontimeupdate = () => syncPlaybackState(analysis);
  audio.onplay = () => syncPlaybackState(analysis);
  audio.onpause = () => syncPlaybackState(analysis);
  audio.onseeked = () => syncPlaybackState(analysis);
  audio.onended = () => {
    state.currentVisualIndex = -1;
    renderChordGrid(analysis);
    void renderGuitarChords(analysis);
    if (dom.playbackStatus) {
      dom.playbackStatus.textContent = "播放已結束";
    }
  };

  if (dom.playbackStatus) {
    dom.playbackStatus.textContent = "可開始播放並同步查看目前和弦位置";
  }
}

function renderChordGrid(analysis) {
  const measures = analysis.measures || [];
  const timeSignature = analysis.summary.timeSignature || 4;
  const measuresPerRow = 4;
  if (!measures.length) {
    dom.chordGrid.innerHTML = '<p class="diagram-note">目前沒有可顯示的小節資料。</p>';
    return;
  }

  const gridHtml = measures
    .map((measure) => measure.cells
      .map((cell, cellIndex) => {
        const classes = [
          "measure-cell",
          cell.isShift ? "shift" : "",
          cell.isPadding ? "padding" : "",
          state.currentVisualIndex === cell.index ? "active" : "",
          cellIndex === 0 ? "measure-start" : "",
          cellIndex === measure.cells.length - 1 ? "measure-end" : "",
        ]
          .filter(Boolean)
          .join(" ");
        const chordLabel = shouldDisplayChordAtIndex(analysis, cell)
          ? formatChordDisplay(cell.chord, state.accidentalPreference)
          : "";
        const seekAttr = cell.time === null || cell.time === undefined ? "" : `data-seek-time="${cell.time}"`;

        return `
          <button class="${classes}" type="button" ${seekAttr}>
            <span class="chord-tag">${chordLabel}</span>
            <span class="time-tag">${state.currentVisualIndex === cell.index && cell.time !== null && cell.time !== undefined ? formatSeconds(cell.time) : ""}</span>
          </button>
        `;
      })
      .join(""))
    .join("");

  dom.chordGrid.innerHTML = `
    <div class="continuous-chord-grid" style="grid-template-columns: repeat(${timeSignature * measuresPerRow}, minmax(0, 1fr));">
      ${gridHtml}
    </div>
  `;

  dom.chordGrid.querySelectorAll("[data-seek-time]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!dom.analysisAudio) return;
      const seekTime = Number(button.dataset.seekTime || 0);
      if (Number.isFinite(seekTime)) {
        dom.analysisAudio.currentTime = seekTime;
        syncPlaybackState(analysis);
      }
    });
  });
}

async function loadChordDb() {
  if (state.chordDb) return state.chordDb;
  const response = await fetch(LOCAL_CHORD_DB_URL);
  if (!response.ok) {
    throw new Error("無法載入本地 chords-db");
  }
  state.chordDb = await response.json();
  return state.chordDb;
}

function buildChordImageCandidates(chordName) {
  const cleanChord = (chordName || "").split("/")[0].trim();
  if (!cleanChord) return [];

  const compact = cleanChord.replace(/\s+/g, "");
  const candidates = [
    compact,
    compact.replace(/:/g, ""),
    compact.replace(/:/g, "-"),
    compact.replace(/#/g, "sharp").replace(/♯/g, "sharp").replace(/♭/g, "flat"),
    compact.replace(/#/g, "s").replace(/♯/g, "s").replace(/♭/g, "b"),
    compact.replace(/[^A-Za-z0-9_-]/g, ""),
  ].filter(Boolean);
  const uniqueNames = [...new Set(candidates)];
  const extensions = ["png", "svg", "jpg", "jpeg", "webp"];
  return uniqueNames.flatMap((name) => extensions.map((ext) => `${LOCAL_CHORD_IMAGE_BASE}/${name}.${ext}`));
}

async function resolveChordDiagramImage(chordName) {
  const candidates = buildChordImageCandidates(chordName);
  for (let index = 0; index < candidates.length; index += 1) {
    try {
      const response = await fetch(candidates[index], { method: "HEAD" });
      if (response.ok) return candidates[index];
    } catch (error) {
      console.debug("Chord image probe failed:", error);
    }
  }
  return null;
}

function parseChordName(chordName) {
  if (!chordName || ["N.C.", "N", "N/C", "NC"].includes(chordName)) return null;

  const cleanChord = chordName.split("/")[0].trim();
  const colonMatch = cleanChord.match(/^([A-G][#b♯♭]?):(.+)$/);
  if (colonMatch) {
    return { root: colonMatch[1], suffix: colonMatch[2] };
  }
  const match = cleanChord.match(/^([A-G])([#b♯♭]?)(.*)$/);
  if (!match) return null;
  return {
    root: `${match[1]}${match[2]}`,
    suffix: match[3] || "",
  };
}

async function getChordDiagramData(chordName) {
  const parsed = parseChordName(chordName);
  if (!parsed) return null;

  const db = await loadChordDb();
  const normalizedRoot = enharmonicMap[parsed.root] || parsed.root;
  const suffixWithoutColon = parsed.suffix ? parsed.suffix.replace(/^:/, "") : "";
  const normalizedSuffix = suffixMap[parsed.suffix] || suffixMap[suffixWithoutColon] || "major";
  const entries = db.chords && db.chords[normalizedRoot] ? db.chords[normalizedRoot] : [];
  return entries.find((item) => item.suffix === normalizedSuffix) || null;
}

function getCurrentChordPosition(chordName) {
  return state.chordPositions[chordName] || 0;
}

function setCurrentChordPosition(chordName, nextIndex) {
  state.chordPositions[chordName] = Math.max(0, nextIndex);
}

function buildChordDiagramSvg(chordData, positionIndex) {
  const position = chordData.positions[Math.min(positionIndex, chordData.positions.length - 1)];
  const svgWidth = 98;
  const svgHeight = 138;
  const left = 26;
  const right = svgWidth - 18;
  const top = 34;
  const bottom = svgHeight - 18;
  const stringSpacing = (right - left) / 5;
  const fretSpacing = (bottom - top) / 4;
  const baseFret = position.baseFret || 1;
  const stringX = Array.from({ length: 6 }, (_, index) => left + stringSpacing * index);
  const frets = position.frets || [];
  const barres = position.barres || [];

  const stringsMarkup = Array.from({ length: 6 }, (_, index) => `<line x1="${stringX[index]}" y1="${top}" x2="${stringX[index]}" y2="${bottom}" class="diagram-string-line" />`).join("");
  const fretsMarkup = Array.from({ length: 5 }, (_, index) => {
    const y = top + fretSpacing * index;
    const lineClass = index === 0 && baseFret === 1 ? "diagram-nut-line" : "diagram-fret-line";
    return `<line x1="${left}" y1="${y}" x2="${right}" y2="${y}" class="${lineClass}" />`;
  }).join("");

  const markersMarkup = frets.map((fret, index) => {
    const x = stringX[index];
    if (fret === -1) return `<text x="${x}" y="18" text-anchor="middle" class="diagram-marker-text">x</text>`;
    if (fret === 0) return `<text x="${x}" y="18" text-anchor="middle" class="diagram-marker-text">o</text>`;
    const relativeFret = Math.max(0, fret - baseFret);
    const y = top + fretSpacing * relativeFret + fretSpacing / 2;
    return `<circle cx="${x}" cy="${y}" r="5" class="diagram-finger-dot" />`;
  }).join("");

  const barreMarkup = barres.map((barreFret) => {
    const matchingStrings = frets
      .map((fret, index) => ({ fret, index }))
      .filter((item) => item.fret === barreFret)
      .map((item) => item.index);
    if (matchingStrings.length < 2) return "";
    const start = stringX[Math.min(...matchingStrings)];
    const end = stringX[Math.max(...matchingStrings)];
    const relativeFret = Math.max(0, barreFret - baseFret);
    const y = top + fretSpacing * relativeFret + fretSpacing / 2;
    return `<line x1="${start}" y1="${y}" x2="${end}" y2="${y}" class="diagram-barre-line" />`;
  }).join("");

  const baseFretLabel = baseFret > 1 ? `<text x="8" y="${top + fretSpacing}" class="diagram-basefret-label">${baseFret}fr</text>` : "";

  return `
    <svg class="diagram-svg" viewBox="0 0 ${svgWidth} ${svgHeight}" role="img" aria-label="${escapeHtml(chordData.key)} chord diagram">
      ${stringsMarkup}
      ${fretsMarkup}
      ${barreMarkup}
      ${markersMarkup}
      ${baseFretLabel}
    </svg>
  `;
}

function getFocusedChordName(analysis) {
  const currentChord = getCurrentChordName(analysis, state.currentVisualIndex);
  if (currentChord) return currentChord;
  const progression = getChordProgression(analysis);
  if (!progression.length) return "";
  if (state.activeProgressionIndex >= progression.length) {
    state.activeProgressionIndex = 0;
  }
  return progression[state.activeProgressionIndex].chord || "";
}

function getChordOrderForSummary(analysis) {
  const uniqueChords = analysis.uniqueChords || [];
  const focusedChord = getFocusedChordName(analysis);
  if (!focusedChord) return uniqueChords;
  return [...uniqueChords].sort((left, right) => {
    if (left === focusedChord) return -1;
    if (right === focusedChord) return 1;
    return left.localeCompare(right);
  });
}

function attachDiagramPositionEvents(analysis) {
  dom.guitarChords.querySelectorAll("[data-chord-nav]").forEach((button) => {
    button.addEventListener("click", () => {
      const chord = button.dataset.chordName;
      const direction = button.dataset.chordNav;
      const total = Number(button.dataset.positionTotal || 1);
      if (!chord || total <= 1) return;
      const current = getCurrentChordPosition(chord);
      const next = direction === "prev" ? (current - 1 + total) % total : (current + 1) % total;
      setCurrentChordPosition(chord, next);
      void renderGuitarChords(analysis);
    });
  });
}

async function renderGuitarChords(analysis) {
  const orderedChords = getChordOrderForSummary(analysis);
  const focusedChord = getFocusedChordName(analysis);
  if (!orderedChords.length) {
    dom.guitarChords.innerHTML = '<p class="diagram-note">目前沒有可顯示的和弦圖。</p>';
    return;
  }

  const cards = await Promise.all(
    orderedChords.map(async (chord) => {
      const chordData = await getChordDiagramData(chord);
      if (chordData && chordData.positions && chordData.positions.length) {
        const positionIndex = Math.min(getCurrentChordPosition(chord), chordData.positions.length - 1);
        return `
          <section class="diagram-card ${focusedChord === chord ? "focused" : ""}">
            <div class="diagram-header">
              <h4>${formatChordDisplay(chord, state.accidentalPreference)}</h4>
              ${focusedChord === chord ? '<span class="diagram-time">當前和弦</span>' : ""}
            </div>
            <div class="diagram-canvas">
              ${buildChordDiagramSvg(chordData, positionIndex)}
            </div>
            ${chordData.positions.length > 1 ? `
              <div class="diagram-position-controls">
                <button type="button" class="diagram-nav-button" data-chord-nav="prev" data-chord-name="${escapeHtml(chord)}" data-position-total="${chordData.positions.length}">‹</button>
                <span>${positionIndex + 1}/${chordData.positions.length}</span>
                <button type="button" class="diagram-nav-button" data-chord-nav="next" data-chord-name="${escapeHtml(chord)}" data-position-total="${chordData.positions.length}">›</button>
              </div>
            ` : ""}
          </section>
        `;
      }

      const imageUrl = await resolveChordDiagramImage(chord);
      if (imageUrl) {
        return `
          <section class="diagram-card ${focusedChord === chord ? "focused" : ""}">
            <div class="diagram-header">
              <h4>${formatChordDisplay(chord, state.accidentalPreference)}</h4>
              ${focusedChord === chord ? '<span class="diagram-time">當前和弦</span>' : ""}
            </div>
            <div class="diagram-image-wrap">
              <img class="diagram-image" src="${imageUrl}" alt="${escapeHtml(chord)} chord diagram" loading="lazy" />
            </div>
          </section>
        `;
      }

      return `
        <section class="diagram-card ${focusedChord === chord ? "focused" : ""}">
          <div class="diagram-header">
            <h4>${formatChordDisplay(chord, state.accidentalPreference)}</h4>
            ${focusedChord === chord ? '<span class="diagram-time">當前和弦</span>' : ""}
          </div>
          <p class="diagram-note">找不到對應的 chord diagram。</p>
        </section>
      `;
    }),
  );

  dom.guitarChords.innerHTML = cards.join("");
  attachDiagramPositionEvents(analysis);
}

async function renderAnalysis(analysis) {
  state.analysis = analysis;
  state.accidentalPreference = getAnalysisAccidentalPreference(analysis);
  state.currentVisualIndex = -1;
  state.activeProgressionIndex = 0;
  dom.resultTitle.textContent = analysis.title;
  dom.resultSubtitle.textContent = `共偵測 ${analysis.summary.totalChords} 個和弦事件，分為整首歌和弦譜、全部和弦指法圖與 Raw Data 三個頁面。`;
  renderSummaryChips(analysis);
  renderSummaryCards(analysis);
  setupAudioPlayer(analysis);
  renderChordGrid(analysis);
  await renderGuitarChords(analysis);
  dom.rawOutput.textContent = JSON.stringify(analysis.raw, null, 2);
  dom.resultsPanel.classList.remove("hidden");
}

async function handleAnalyze() {
  const formData = new FormData();
  formData.append("beat_detector", FIXED_BEAT_DETECTOR);
  formData.append("chord_detector", FIXED_CHORD_DETECTOR);

  if (state.selectedFile) {
    formData.append("audio_file", state.selectedFile);
  } else if (state.selectedVideo) {
    formData.append("video_id", state.selectedVideo.id);
    formData.append("title", state.selectedVideo.title);
  } else {
    return;
  }

  dom.resultsPanel.classList.add("hidden");
  dom.analyzeButton.disabled = true;
  startLoading();

  try {
    const response = await fetch("/api/analyze", {
      method: "POST",
      body: formData,
    });
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.error || "分析失敗");
    }
    await renderAnalysis(data.analysis);
  } catch (error) {
    window.alert(error.message || "分析失敗");
  } finally {
    stopLoading();
    updateSelectedSource();
  }
}

function setupTabs() {
  dom.tabs.forEach((button) => {
    button.addEventListener("click", () => {
      const target = button.dataset.tab;
      dom.tabs.forEach((item) => item.classList.toggle("active", item === button));
      dom.panels.forEach((panel) => {
        panel.classList.toggle("active", panel.id === `tab-${target}`);
      });
    });
  });
}

function setupFileInput() {
  dom.audioFile.addEventListener("change", () => {
    const file = dom.audioFile.files[0];
    state.selectedFile = file || null;
    state.selectedVideo = null;
    dom.fileMeta.textContent = file ? `${file.name} | ${formatBytes(file.size)}` : "";
    renderSearchResults();
    updateSelectedSource();
  });
}

dom.searchForm.addEventListener("submit", handleSearch);
dom.analyzeButton.addEventListener("click", handleAnalyze);
setupTabs();
setupFileInput();
updateSelectedSource();
loadEngineStatus();
