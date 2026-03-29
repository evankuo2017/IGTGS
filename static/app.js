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
  capo: 0, // Capo 0-5，預設 0 表示無移調
  /** 是否在和弦譜上以紅框標示有 refine 的格子（與分頁列 Refine result 連動） */
  refineHighlightActive: false,
  /** 點擊譜面後下一次 draw：藍線精確落在點擊的 x／列（避免 time 反算與格子對齊誤差） */
  playheadClickSnap: null,
  /** 播放中用 requestAnimationFrame 持續更新 playhead（比 timeupdate 流暢） */
  playheadRafId: null,
  /** setupAudioPlayer 以 blob: 載入時需 revoke，避免洩漏與重複指派失敗 */
  playbackObjectUrl: null,
};

const FIXED_BEAT_DETECTOR = "madmom";
const FIXED_CHORD_DETECTOR = "chord-cnn-lstm";

const LOCAL_CHORD_DB_URL = "/static/vendor/chords/guitar.json";
const LOCAL_CHORD_IMAGE_BASE = "/static/chord-diagrams";

/** localStorage：即時指形圖是否收合（跨重新整理保留） */
const LS_CHORD_SIDEBAR_COLLAPSED = "igtgsChordSidebarCollapsed";

/** 和弦譜橫向一列放幾個小節（與 renderChordGrid 一致） */
const CHORD_GRID_MEASURES_PER_ROW = 4;

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
  chordSidebar: document.querySelector("#chord-sidebar"),
  chordSidebarToggle: document.querySelector("#chord-sidebar-toggle"),
  resultTitle: document.querySelector("#result-title"),
  resultSubtitle: document.querySelector("#result-subtitle"),
  summaryChips: document.querySelector("#summary-chips"),
  analysisSummary: document.querySelector("#analysis-summary"),
  analysisAudio: document.querySelector("#analysis-audio"),
  playbackStatus: document.querySelector("#playback-status"),
  chordGrid: document.querySelector("#chord-grid"),
  currentChordDiagram: document.querySelector("#current-chord-diagram"),
  nextChordDiagram: document.querySelector("#next-chord-diagram"),
  guitarChords: document.querySelector("#guitar-chords"),
  capoSelector: document.querySelector("#capo-selector"),
  refineResultToggle: document.querySelector("#refine-result-toggle"),
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

/**
 * 將和弦移調指定半音數（用於 Capo 功能）
 * @param {string} chordName - 原始和弦名稱，如 "C:maj"、"F#:min7/G"
 * @param {number} semitones - 降低半音數（Capo 1 = 降 1、Capo 2 = 降 2，依此類推）
 * @param {string} [accidentalPreference] - 升記號 "sharp" 或降記號 "flat"
 * @returns {string} 移調後的和弦名稱
 */
function transposeChord(chordName, semitones, accidentalPreference) {
  if (semitones === 0 || !chordName || ["N", "N/C", "N.C.", "NC"].includes(chordName)) {
    return chordName;
  }

  const notes = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
  const notesWithFlats = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"];
  const normalize = (n) => normalizeAccidentals(n);
  const noteIndex = (note, useFlats) => {
    const arr = useFlats ? notesWithFlats : notes;
    const idx = arr.findIndex((x) => normalize(x) === normalize(note));
    return idx >= 0 ? idx : notes.indexOf(note);
  };
  const semitoneIndex = (idx, delta, useFlats) => {
    const arr = useFlats ? notesWithFlats : notes;
    return arr[(idx - delta + 12) % 12]; // delta 為正數時表示降半音（Capo 1 = 降 1）
  };

  const parts = chordName.split("/");
  const mainPart = (parts[0] || "").trim();
  const bassPart = (parts[1] || "").trim();

  let root = "";
  let quality = "";
  if (mainPart.includes(":")) {
    const [r, ...q] = mainPart.split(":");
    root = r || "";
    quality = q.join(":");
  } else {
    const match = mainPart.match(/^([A-G][#b♯♭]?)(.*)$/);
    if (match) {
      root = match[1] || "";
      quality = match[2] || "";
    } else {
      root = mainPart;
    }
  }

  const useFlats = accidentalPreference === "flat" || (root.includes("b") && accidentalPreference !== "sharp");
  const rootIdx = noteIndex(root, useFlats);
  if (rootIdx < 0) return chordName;

  const newRoot = semitoneIndex(rootIdx, semitones, useFlats);
  let mainResult = quality ? `${newRoot}:${quality}` : newRoot;

  if (bassPart && /^[A-G][#b♯♭]?$/.test(normalize(bassPart))) {
    const bassIdx = noteIndex(bassPart, useFlats);
    if (bassIdx >= 0) {
      const newBass = semitoneIndex(bassIdx, semitones, useFlats);
      mainResult += `/${newBass}`;
    } else {
      mainResult += `/${bassPart}`;
    }
  } else if (bassPart) {
    mainResult += `/${bassPart}`;
  }

  return mainResult;
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

/**
 * 將即時指形圖面板固定在 viewport（視窗）底部中央。
 * 注意：若 #chord-sidebar 放在含 backdrop-filter 的 .panel 內，fixed 會失效（相對面板），故 HTML 已移出面板。
 */
function pinChordSidebar() {
  const el = dom.chordSidebar;
  if (!el) return;

  el.style.position = "fixed";
  el.style.bottom = "calc(12px + env(safe-area-inset-bottom))";
  el.style.left = "50%";
  el.style.right = "auto";
  el.style.top = "auto";
  el.style.transform = "translateX(-50%)";
  el.style.zIndex = "20";
}

function readChordSidebarCollapsedPreference() {
  try {
    return window.localStorage.getItem(LS_CHORD_SIDEBAR_COLLAPSED) === "1";
  } catch {
    return false;
  }
}

function writeChordSidebarCollapsedPreference(collapsed) {
  try {
    window.localStorage.setItem(LS_CHORD_SIDEBAR_COLLAPSED, collapsed ? "1" : "0");
  } catch {
    /* ignore quota / private mode */
  }
}

/**
 * 套用即時指形圖收合／展開 UI（與 localStorage 同步）
 * @param {boolean} collapsed 是否收合為僅標題列
 */
function applyChordSidebarCollapsed(collapsed) {
  const panel = dom.chordSidebar;
  const btn = dom.chordSidebarToggle;
  if (!panel || !btn) return;

  panel.classList.toggle("chord-sidebar-collapsed", collapsed);
  btn.setAttribute("aria-expanded", collapsed ? "false" : "true");
  btn.title = collapsed ? "展開即時指形圖" : "收合即時指形圖";
  const label = btn.querySelector(".chord-sidebar-toggle-label");
  if (label) {
    label.textContent = collapsed ? "展開" : "收合";
  }
  writeChordSidebarCollapsedPreference(collapsed);
}

function setupChordSidebarCollapse() {
  const panel = dom.chordSidebar;
  const btn = dom.chordSidebarToggle;
  if (!panel || !btn) return;

  applyChordSidebarCollapsed(readChordSidebarCollapsedPreference());

  btn.addEventListener("click", () => {
    const nowCollapsed = panel.classList.contains("chord-sidebar-collapsed");
    applyChordSidebarCollapsed(!nowCollapsed);
  });
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

/**
 * 將 measures 展平成依 cell.index 排序的陣列（用於 playhead 與時間插值）
 */
function flattenGridCells(analysis) {
  const measures = analysis.measures || [];
  const out = [];
  measures.forEach((measure) => {
    measure.cells.forEach((cell) => {
      out.push(cell);
    });
  });
  out.sort((a, b) => a.index - b.index);
  return out;
}

/**
 * 取得有有效 time 的 beat cell 列表（依時間排序）
 */
function getChordGridColsPerRow(analysis) {
  const ts = analysis && analysis.summary ? analysis.summary.timeSignature || 4 : 4;
  return ts * CHORD_GRID_MEASURES_PER_ROW;
}

/**
 * 取得某一列在 track 內的 top / height（與 getBoundingClientRect 對齊 scrollTop）
 */
function getRowPixelBoundsRelativeToTrack(track, analysis, rowIndex) {
  const colsPerRow = getChordGridColsPerRow(analysis);
  const start = rowIndex * colsPerRow;
  const end = start + colsPerRow - 1;
  const tr = track.getBoundingClientRect();
  let minTop = Infinity;
  let maxBottom = -Infinity;
  for (let i = start; i <= end; i += 1) {
    const el = track.querySelector(`[data-cell-index="${i}"]`);
    if (!el) continue;
    const r = el.getBoundingClientRect();
    minTop = Math.min(minTop, r.top);
    maxBottom = Math.max(maxBottom, r.bottom);
  }
  if (minTop === Infinity) return null;
  return {
    top: minTop - tr.top + track.scrollTop,
    height: Math.max(4, maxBottom - minTop),
  };
}

/**
 * 該列所有格子的水平範圍（含 padding 格），用於點擊與 playhead 插值
 */
function getRowXContentSpanInTrack(track, analysis, rowIndex) {
  const colsPerRow = getChordGridColsPerRow(analysis);
  const start = rowIndex * colsPerRow;
  const end = start + colsPerRow - 1;
  const tr = track.getBoundingClientRect();
  let minX = Infinity;
  let maxX = -Infinity;
  for (let i = start; i <= end; i += 1) {
    const el = track.querySelector(`[data-cell-index="${i}"]`);
    if (!el) continue;
    const r = el.getBoundingClientRect();
    minX = Math.min(minX, r.left - tr.left + track.scrollLeft);
    maxX = Math.max(maxX, r.right - tr.left + track.scrollLeft);
  }
  if (minX === Infinity) return null;
  return { minX, maxX };
}

/**
 * 列內依「格子左緣 x」排序的 time anchor（僅有 beat time 的格）
 */
function collectRowTimeAnchorsInTrack(track, analysis, rowIndex) {
  const colsPerRow = getChordGridColsPerRow(analysis);
  const start = rowIndex * colsPerRow;
  const end = start + colsPerRow - 1;
  const tr = track.getBoundingClientRect();
  const cells = flattenGridCells(analysis);
  const anchors = [];
  for (let idx = start; idx <= end; idx += 1) {
    const el = track.querySelector(`[data-cell-index="${idx}"]`);
    if (!el) continue;
    const c = cells.find((x) => x.index === idx);
    if (!c || c.time === null || c.time === undefined || !Number.isFinite(Number(c.time))) {
      continue;
    }
    const r = el.getBoundingClientRect();
    const x = r.left - tr.left + track.scrollLeft;
    anchors.push({ x, time: Number(c.time), index: idx });
  }
  anchors.sort((a, b) => a.x - b.x || a.index - b.index);
  return anchors;
}

/** 下一列第一個有 time 的 anchor 時間（用於本列尾端 time→x 的 endT，避免誤用整首 duration 導致藍線卡在列尾） */
function getNextRowFirstAnchorTime(track, analysis, rowIndex) {
  const anchors = collectRowTimeAnchorsInTrack(track, analysis, rowIndex + 1);
  if (!anchors.length) return null;
  return anchors[0].time;
}

/** 上一列最後一個 anchor 時間（用於本列開頭與點擊反算） */
function getPrevRowLastAnchorTime(track, analysis, rowIndex) {
  if (rowIndex <= 0) return null;
  const anchors = collectRowTimeAnchorsInTrack(track, analysis, rowIndex - 1);
  if (!anchors.length) return null;
  return anchors[anchors.length - 1].time;
}

/**
 * 由點擊的水平位置（track 內容座標）對應 seek 時間（與 timeToXInRow 互為反函數）
 */
function xInTrackToTimeInRow(track, analysis, rowIndex, xInTrack) {
  const anchors = collectRowTimeAnchorsInTrack(track, analysis, rowIndex);
  const span = getRowXContentSpanInTrack(track, analysis, rowIndex);
  const audio = dom.analysisAudio;
  const duration = audio && Number.isFinite(audio.duration) ? audio.duration : null;

  if (!anchors.length || !span) return null;

  const { minX: rowMin, maxX: rowMax } = span;
  const x = Math.max(rowMin, Math.min(rowMax, xInTrack));

  if (x <= anchors[0].x) {
    const prevLast = getPrevRowLastAnchorTime(track, analysis, rowIndex);
    if (prevLast !== null && prevLast < anchors[0].time) {
      const denom = anchors[0].x - rowMin;
      if (denom <= 0) return prevLast;
      const frac = (x - rowMin) / denom;
      return prevLast + Math.max(0, Math.min(1, frac)) * (anchors[0].time - prevLast);
    }
    const denom = anchors[0].x - rowMin;
    if (denom <= 0) return Math.max(0, anchors[0].time);
    const frac = (x - rowMin) / denom;
    return Math.max(0, anchors[0].time * frac);
  }

  const last = anchors[anchors.length - 1];
  if (x >= last.x) {
    const nextFirst = getNextRowFirstAnchorTime(track, analysis, rowIndex);
    const endT =
      nextFirst !== null && nextFirst > last.time
        ? nextFirst
        : duration && duration > last.time
          ? duration
          : last.time + 0.25;
    const denom = rowMax - last.x;
    if (denom <= 0) return last.time;
    const frac = (x - last.x) / denom;
    return Math.min(endT, last.time + frac * (endT - last.time));
  }

  for (let i = 0; i < anchors.length - 1; i += 1) {
    const A = anchors[i];
    const B = anchors[i + 1];
    if (x < B.x) {
      const denom = B.x - A.x;
      const frac = denom > 0 ? (x - A.x) / denom : 0;
      return A.time + frac * (B.time - A.time);
    }
  }
  return last.time;
}

/**
 * 由播放時間反算 playhead 水平位置（與 xInTrackToTimeInRow 同一套幾何）
 */
function timeToXInTrackInRow(track, analysis, rowIndex, t) {
  const anchors = collectRowTimeAnchorsInTrack(track, analysis, rowIndex);
  const span = getRowXContentSpanInTrack(track, analysis, rowIndex);
  const audio = dom.analysisAudio;
  const duration = audio && Number.isFinite(audio.duration) ? audio.duration : null;

  if (!anchors.length || !span) return null;

  const { minX: rowMin, maxX: rowMax } = span;
  const time = Math.max(0, t || 0);

  if (time <= anchors[0].time) {
    const prevLast = getPrevRowLastAnchorTime(track, analysis, rowIndex);
    if (prevLast !== null && prevLast < anchors[0].time) {
      const denom = anchors[0].time - prevLast;
      if (denom <= 0) return anchors[0].x;
      const frac = (time - prevLast) / denom;
      return rowMin + Math.max(0, Math.min(1, frac)) * (anchors[0].x - rowMin);
    }
    const denom = anchors[0].time;
    if (denom <= 0) return rowMin;
    const frac = time / denom;
    return rowMin + frac * (anchors[0].x - rowMin);
  }

  const last = anchors[anchors.length - 1];
  if (time >= last.time) {
    const nextFirst = getNextRowFirstAnchorTime(track, analysis, rowIndex);
    const endT =
      nextFirst !== null && nextFirst > last.time
        ? nextFirst
        : duration && duration > last.time
          ? duration
          : last.time + 0.25;
    const denom = endT - last.time;
    if (denom <= 0) return last.x;
    const frac = (time - last.time) / denom;
    return last.x + Math.max(0, Math.min(1, frac)) * (rowMax - last.x);
  }

  for (let i = 0; i < anchors.length - 1; i += 1) {
    const A = anchors[i];
    const B = anchors[i + 1];
    if (time < B.time) {
      const denom = B.time - A.time;
      const frac = denom > 0 ? (time - A.time) / denom : 0;
      return A.x + frac * (B.x - A.x);
    }
  }
  return last.x;
}

/**
 * 由螢幕 Y 判斷點在哪一列（viewport 座標）
 */
function rowIndexFromPointerClientY(track, analysis, clientY) {
  const colsPerRow = getChordGridColsPerRow(analysis);
  const cells = flattenGridCells(analysis);
  const maxIdx = cells.reduce((m, c) => Math.max(m, c.index), 0);
  const maxRow = Math.floor(maxIdx / colsPerRow);
  for (let row = 0; row <= maxRow; row += 1) {
    const start = row * colsPerRow;
    const end = start + colsPerRow - 1;
    let minTop = Infinity;
    let maxBottom = -Infinity;
    for (let i = start; i <= end; i += 1) {
      const el = track.querySelector(`[data-cell-index="${i}"]`);
      if (!el) continue;
      const r = el.getBoundingClientRect();
      minTop = Math.min(minTop, r.top);
      maxBottom = Math.max(maxBottom, r.bottom);
    }
    if (minTop === Infinity) continue;
    if (clientY >= minTop && clientY < maxBottom) {
      return row;
    }
  }
  return null;
}

/**
 * 計算 playhead 在 chord-grid-track 內的水平座標（px，相對於可捲動內容左緣；與點擊座標同一套列內插值）
 */
function computeChordPlayheadX(track, analysis, currentTime) {
  if (!track || !analysis) return null;
  const colsPerRow = getChordGridColsPerRow(analysis);
  let vi = getCurrentVisualIndexForTime(analysis, currentTime || 0);
  if (vi < 0) vi = 0;
  const row = Math.floor(vi / colsPerRow);
  return timeToXInTrackInRow(track, analysis, row, Math.max(0, currentTime || 0));
}

/**
 * 計算目前播放時間對應的「列」在 track 內的垂直範圍（playhead 只畫在該列內）
 */
function computeChordPlayheadRowVerticalLayout(track, analysis, currentTime) {
  const colsPerRow = getChordGridColsPerRow(analysis);
  let vi = getCurrentVisualIndexForTime(analysis, currentTime || 0);
  if (vi < 0) vi = 0;
  const row = Math.floor(vi / colsPerRow);
  return getRowPixelBoundsRelativeToTrack(track, analysis, row);
}

/**
 * 更新和弦譜上的垂直 playhead（播放位置線），不重新渲染整個 grid。
 */
/**
 * 播放時以 rAF 高頻率更新藍線；暫停時停止以省資源。
 */
function chordPlayheadRafTick() {
  state.playheadRafId = null;
  if (!dom.analysisAudio || dom.analysisAudio.paused || !state.analysis) {
    return;
  }
  updateChordPlayhead(state.analysis);
  state.playheadRafId = window.requestAnimationFrame(chordPlayheadRafTick);
}

function startChordPlayheadRaf() {
  stopChordPlayheadRaf();
  if (!dom.analysisAudio || dom.analysisAudio.paused || !state.analysis) {
    return;
  }
  state.playheadRafId = window.requestAnimationFrame(chordPlayheadRafTick);
}

function stopChordPlayheadRaf() {
  if (state.playheadRafId !== null) {
    window.cancelAnimationFrame(state.playheadRafId);
    state.playheadRafId = null;
  }
}

function updateChordPlayhead(analysis) {
  const track = dom.chordGrid?.querySelector(".chord-grid-track");
  const playhead = dom.chordGrid?.querySelector(".chord-playhead");
  if (!track || !playhead || !analysis) {
    return;
  }

  if (!dom.analysisAudio || !analysis.playbackUrl) {
    playhead.style.display = "none";
    return;
  }

  const t = dom.analysisAudio.currentTime || 0;

  if (state.playheadClickSnap) {
    const snap = state.playheadClickSnap;
    state.playheadClickSnap = null;
    const rowLayout = getRowPixelBoundsRelativeToTrack(track, analysis, snap.row);
    if (rowLayout !== null) {
      playhead.style.display = "block";
      playhead.style.left = `${snap.x}px`;
      playhead.style.top = `${rowLayout.top}px`;
      playhead.style.height = `${rowLayout.height}px`;
      playhead.style.bottom = "auto";
      return;
    }
  }

  const x = computeChordPlayheadX(track, analysis, t);
  const rowLayout = computeChordPlayheadRowVerticalLayout(track, analysis, t);
  if (x === null || rowLayout === null) {
    playhead.style.display = "none";
    return;
  }

  playhead.style.display = "block";
  playhead.style.left = `${x}px`;
  playhead.style.top = `${rowLayout.top}px`;
  playhead.style.height = `${rowLayout.height}px`;
  playhead.style.bottom = "auto";
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
  const previousVisualIndex = state.currentVisualIndex;
  state.currentVisualIndex = getCurrentVisualIndexForTime(analysis, currentTime);
  const currentChord = getCurrentChordName(analysis, state.currentVisualIndex);

  if (currentChord) {
    const progression = getChordProgression(analysis);
    let bestIndex = -1;
    let bestTime = -Infinity;
    progression.forEach((item, index) => {
      if (item.time === null || item.time === undefined) {
        return;
      }
      if (item.time <= currentTime && item.time >= bestTime) {
        bestTime = item.time;
        bestIndex = index;
      }
    });
    if (bestIndex >= 0) {
      state.activeProgressionIndex = bestIndex;
    }
  }

  if (previousVisualIndex !== state.currentVisualIndex) {
    void renderGuitarChords(analysis);
    void renderCurrentNextChordDiagrams(analysis);
  }

  updateChordPlayhead(analysis);

  if (dom.playbackStatus) {
    const currentLabel = formatSeconds(currentTime);
    const displayChord = state.capo
      ? transposeChord(currentChord, state.capo, state.accidentalPreference)
      : currentChord;
    const chordLabel = currentChord
      ? `目前和弦：${stripHtml(formatChordDisplay(displayChord, state.accidentalPreference))}`
      : "目前尚未進入和弦區段";
    dom.playbackStatus.textContent = `${currentLabel} · ${chordLabel}`;
  }
}

function revokePlaybackObjectUrl() {
  if (state.playbackObjectUrl) {
    try {
      URL.revokeObjectURL(state.playbackObjectUrl);
    } catch {
      /* ignore */
    }
    state.playbackObjectUrl = null;
  }
}

/**
 * 優先以 fetch → Blob → blob: URL 載入，讓瀏覽器依 HTTP Content-Type 解碼，避開 <audio src> 對 webm/副檔名的相容問題。
 */
async function setupAudioPlayer(analysis) {
  if (!dom.analysisAudio) return;

  const audio = dom.analysisAudio;
  stopChordPlayheadRaf();
  audio.pause();
  audio.currentTime = 0;
  state.currentVisualIndex = -1;
  audio.onerror = null;
  revokePlaybackObjectUrl();

  if (!analysis.playbackUrl) {
    audio.removeAttribute("src");
    audio.querySelectorAll("source").forEach((el) => el.remove());
    audio.load();
    if (dom.playbackStatus) {
      dom.playbackStatus.textContent = "目前來源沒有可播放音訊";
    }
    return;
  }

  const rawUrl = analysis.playbackUrl;
  const absoluteUrl =
    rawUrl.startsWith("http://") || rawUrl.startsWith("https://") || rawUrl.startsWith("//")
      ? rawUrl
      : `${window.location.origin}${rawUrl.startsWith("/") ? "" : "/"}${rawUrl}`;

  audio.querySelectorAll("source").forEach((el) => el.remove());
  audio.removeAttribute("src");

  if (dom.playbackStatus) {
    dom.playbackStatus.textContent = "正在載入音訊…";
  }

  let loadError = null;
  try {
    const res = await fetch(absoluteUrl, {
      credentials: "same-origin",
      cache: "no-store",
    });
    if (!res.ok) {
      throw new Error(`HTTP ${res.status} ${res.statusText || ""}`.trim());
    }
    const blob = await res.blob();
    if (!blob.size) {
      throw new Error("音訊檔大小為 0");
    }
    state.playbackObjectUrl = URL.createObjectURL(blob);
    audio.src = state.playbackObjectUrl;
  } catch (err) {
    loadError = err;
    console.warn("IGTGS: fetch→blob 載入音訊失敗，改為直接 URL", err);
    audio.src = absoluteUrl;
    if (dom.playbackStatus) {
      dom.playbackStatus.textContent = `已改為直接連線載入。若仍無法播放：${err instanceof Error ? err.message : String(err)}`;
    }
  }

  audio.load();

  audio.onerror = () => {
    if (!dom.playbackStatus) return;
    const code = audio.error ? audio.error.code : "?";
    const mediaErr =
      audio.error && audio.error.message ? `（${audio.error.message}）` : "";
    const extra = loadError
      ? `；fetch 階段：${loadError instanceof Error ? loadError.message : String(loadError)}`
      : "";
    dom.playbackStatus.textContent = `音訊無法播放：媒體錯誤碼 ${code}${mediaErr}。請確認 Network 中 /media 為 200、伺服器與瀏覽器同源，或改上傳 MP3。${extra}`;
  };

  audio.ontimeupdate = () => syncPlaybackState(analysis);
  audio.onplay = () => {
    if (dom.playbackStatus) {
      dom.playbackStatus.textContent = "可開始播放並同步查看目前和弦位置";
    }
    syncPlaybackState(analysis);
    startChordPlayheadRaf();
  };
  audio.onpause = () => {
    stopChordPlayheadRaf();
    syncPlaybackState(analysis);
  };
  audio.onseeked = () => syncPlaybackState(analysis);
  audio.onended = () => {
    stopChordPlayheadRaf();
    state.currentVisualIndex = -1;
    void renderGuitarChords(analysis);
    updateChordPlayhead(analysis);
    if (dom.playbackStatus) {
      dom.playbackStatus.textContent = "播放已結束";
    }
  };

  if (dom.playbackStatus && !loadError) {
    dom.playbackStatus.textContent = "可開始播放並同步查看目前和弦位置";
  }
}

function getRefinedGridVisualIndexSet(analysis) {
  const ref = analysis && analysis.raw && analysis.raw.chordRefine;
  const ids = ref && Array.isArray(ref.refinedGridVisualIndices) ? ref.refinedGridVisualIndices : [];
  return new Set(ids.map((n) => Number(n)));
}

function syncRefineHighlightToggleUi(analysis) {
  const btn = dom.refineResultToggle;
  if (!btn) return;
  const ref = analysis && analysis.raw && analysis.raw.chordRefine;
  const beats = ref && Array.isArray(ref.beats) ? ref.beats : [];
  const hasReport = beats.length > 0;
  const refinedSet = getRefinedGridVisualIndexSet(analysis);
  const hasRefined = refinedSet.size > 0;
  const serverHint =
    analysis && typeof analysis.refineUserHint === "string" && analysis.refineUserHint.trim()
      ? analysis.refineUserHint.trim()
      : "";

  // 只要有 chordRefine 逐拍報告即可按（音檔與 refine 是否成功改由點擊或 tooltip 說明）
  btn.disabled = !hasReport;

  if (!hasRefined) {
    state.refineHighlightActive = false;
    btn.setAttribute("aria-pressed", "false");
    btn.classList.remove("active");
  }

  if (!hasReport) {
    btn.title = "請先完成分析後再使用 Refine result。";
    return;
  }

  if (hasRefined) {
    btn.title = "顯示／隱藏有經 Refiner 更新的和弦格（紅框）";
  } else {
    const base =
      serverHint ||
      "目前沒有任何拍通過 ChordRefiner。請確認 igtgs_backend/models/ChordRefiner/best_chord_model.pth，且該拍為 maj/maj7/min/min7、refiner 信心≥0.5。詳見 Raw Data → chordRefine。";
    btn.title = `本次無可標示的紅框格：${base}（點擊可再次顯示說明）`;
  }
}

function renderChordGrid(analysis) {
  const measures = analysis.measures || [];
  const timeSignature = analysis.summary.timeSignature || 4;
  const measuresPerRow = CHORD_GRID_MEASURES_PER_ROW;
  const colsPerRow = timeSignature * measuresPerRow;
  if (!measures.length) {
    dom.chordGrid.innerHTML = '<p class="diagram-note">目前沒有可顯示的小節資料。</p>';
    return;
  }

  const refinedVisual = getRefinedGridVisualIndexSet(analysis);

  let maxCellIndex = -1;
  measures.forEach((measure) => {
    measure.cells.forEach((cell) => {
      maxCellIndex = Math.max(maxCellIndex, cell.index);
    });
  });

  const gridHtml = measures
    .map((measure) => measure.cells
      .map((cell, cellIndex) => {
        const isLastCellInGridRow = cell.index % colsPerRow === colsPerRow - 1;
        const isRowWrapToNextLine = isLastCellInGridRow && cell.index < maxCellIndex;
        const refineHighlight =
          state.refineHighlightActive && refinedVisual.has(cell.index) ? "refine-highlight" : "";
        const classes = [
          "measure-cell",
          cell.isShift ? "shift" : "",
          cell.isPadding ? "padding" : "",
          cellIndex === 0 ? "measure-start" : "",
          isRowWrapToNextLine ? "chord-grid-row-wrap-bar" : "",
          Math.floor(cell.index / colsPerRow) >= 1 ? "chord-grid-row-hline" : "",
          refineHighlight,
        ]
          .filter(Boolean)
          .join(" ");
        const displayChord = state.capo
          ? transposeChord(cell.chord, state.capo, state.accidentalPreference)
          : cell.chord;
        const chordLabel = shouldDisplayChordAtIndex(analysis, cell)
          ? formatChordDisplay(displayChord, state.accidentalPreference)
          : "";
        const seekAttr = cell.time === null || cell.time === undefined ? "" : `data-seek-time="${cell.time}"`;

        return `
          <button class="${classes}" type="button" data-cell-index="${cell.index}" ${seekAttr}>
            <span class="chord-tag">${chordLabel}</span>
            <span class="time-tag" aria-hidden="true"></span>
          </button>
        `;
      })
      .join(""))
    .join("");

  dom.chordGrid.innerHTML = `
    <div class="chord-grid-track">
      <div class="continuous-chord-grid" style="grid-template-columns: repeat(${timeSignature * measuresPerRow}, minmax(0, 1fr));">
        ${gridHtml}
      </div>
      <div class="chord-playhead" aria-hidden="true"></div>
    </div>
  `;
  const trackEl = dom.chordGrid.querySelector(".chord-grid-track");
  if (trackEl) {
    trackEl.addEventListener(
      "scroll",
      () => {
        updateChordPlayhead(analysis);
      },
      { passive: true },
    );
  }
  window.requestAnimationFrame(() => updateChordPlayhead(analysis));
}

function setupChordGridSeek() {
  dom.chordGrid.addEventListener("pointerdown", (event) => {
    if (!state.analysis || !dom.analysisAudio) return;
    const track = dom.chordGrid.querySelector(".chord-grid-track");
    if (!track || !(event.target instanceof Element)) return;
    if (!track.contains(event.target)) return;

    const row = rowIndexFromPointerClientY(track, state.analysis, event.clientY);
    if (row === null) return;

    const tr = track.getBoundingClientRect();
    const xInTrack = event.clientX - tr.left + track.scrollLeft;
    const seekTime = xInTrackToTimeInRow(track, state.analysis, row, xInTrack);
    if (seekTime === null || !Number.isFinite(seekTime)) return;

    const dur = dom.analysisAudio.duration;
    let clamped = Math.max(0, seekTime);
    if (Number.isFinite(dur) && dur > 0) {
      clamped = Math.min(clamped, dur);
    }

    state.playheadClickSnap = { x: xInTrack, row };
    dom.analysisAudio.currentTime = clamped;
    syncPlaybackState(state.analysis);
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

/**
 * 取得下一個和弦名稱（用於側邊欄「下個和弦」指形圖）
 * @param {Object} analysis - 分析結果
 * @returns {string} 下一個和弦名稱，若無則回傳空字串
 */
function getNextChordName(analysis) {
  const progression = getChordProgression(analysis);
  if (!progression.length) return "";
  const nextIndex = state.activeProgressionIndex + 1;
  if (nextIndex >= progression.length) return "";
  return progression[nextIndex].chord || "";
}

/**
 * 將單一和弦指形圖渲染至指定容器（用於側邊欄）
 * @param {HTMLElement} container - 目標 DOM 元素
 * @param {string} chordName - 和弦名稱（如 "C:maj"）
 * @param {string} placeholder - 無和弦時顯示的文字
 */
async function renderSingleChordDiagram(container, chordName, placeholder) {
  if (!container) return;
  if (!chordName || ["N", "N/C", "N.C.", "NC"].includes(chordName)) {
    container.innerHTML = `<p class="chord-sidebar-empty">${placeholder}</p>`;
    return;
  }

  const displayChord = state.capo
    ? transposeChord(chordName, state.capo, state.accidentalPreference)
    : chordName;

  const chordData = await getChordDiagramData(displayChord);
  if (chordData && chordData.positions && chordData.positions.length) {
    const positionIndex = Math.min(getCurrentChordPosition(chordName), chordData.positions.length - 1);
    container.innerHTML = `
      <div class="chord-sidebar-diagram-inner">
        <div class="chord-sidebar-chord-name">${formatChordDisplay(displayChord, state.accidentalPreference)}</div>
        <div class="chord-sidebar-svg-wrap">${buildChordDiagramSvg(chordData, positionIndex)}</div>
      </div>
    `;
    return;
  }

  const imageUrl = await resolveChordDiagramImage(displayChord);
  if (imageUrl) {
    container.innerHTML = `
      <div class="chord-sidebar-diagram-inner">
        <div class="chord-sidebar-chord-name">${formatChordDisplay(displayChord, state.accidentalPreference)}</div>
        <div class="chord-sidebar-image-wrap">
          <img class="chord-sidebar-image" src="${imageUrl}" alt="${escapeHtml(displayChord)} chord" loading="lazy" />
        </div>
      </div>
    `;
    return;
  }

  container.innerHTML = `
    <div class="chord-sidebar-diagram-inner">
      <div class="chord-sidebar-chord-name">${formatChordDisplay(displayChord, state.accidentalPreference)}</div>
      <p class="chord-sidebar-empty">找不到指形圖</p>
    </div>
  `;
}

/**
 * 渲染側邊欄：當前和弦與下個和弦的指形圖
 * @param {Object} analysis - 分析結果
 */
async function renderCurrentNextChordDiagrams(analysis) {
  if (!analysis || !dom.currentChordDiagram || !dom.nextChordDiagram) return;

  const currentChord = getCurrentChordName(analysis, state.currentVisualIndex) || getFocusedChordName(analysis);
  const nextChord = getNextChordName(analysis);

  await Promise.all([
    renderSingleChordDiagram(dom.currentChordDiagram, currentChord, "尚未進入和弦區段"),
    renderSingleChordDiagram(dom.nextChordDiagram, nextChord, "無下一個和弦"),
  ]);
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
      const displayChord = state.capo
        ? transposeChord(chord, state.capo, state.accidentalPreference)
        : chord;
      const chordData = await getChordDiagramData(displayChord);
      if (chordData && chordData.positions && chordData.positions.length) {
        const positionIndex = Math.min(getCurrentChordPosition(chord), chordData.positions.length - 1);
        return `
          <section class="diagram-card ${focusedChord === chord ? "focused" : ""}">
            <div class="diagram-header">
              <h4>${formatChordDisplay(displayChord, state.accidentalPreference)}</h4>
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

      const imageUrl = await resolveChordDiagramImage(displayChord);
      if (imageUrl) {
        return `
          <section class="diagram-card ${focusedChord === chord ? "focused" : ""}">
            <div class="diagram-header">
              <h4>${formatChordDisplay(displayChord, state.accidentalPreference)}</h4>
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
            <h4>${formatChordDisplay(displayChord, state.accidentalPreference)}</h4>
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
  state.capo = 0;
  state.refineHighlightActive = false;
  if (dom.capoSelector) dom.capoSelector.value = "0";
  if (dom.refineResultToggle) {
    dom.refineResultToggle.setAttribute("aria-pressed", "false");
    dom.refineResultToggle.classList.remove("active");
  }
  syncRefineHighlightToggleUi(analysis);
  dom.resultTitle.textContent = analysis.title;
  dom.resultSubtitle.textContent = `共偵測 ${analysis.summary.totalChords} 個和弦事件，分為整首歌和弦譜、全部和弦指法圖與 Raw Data 三個頁面。`;
  renderSummaryChips(analysis);
  renderSummaryCards(analysis);
  await setupAudioPlayer(analysis);
  renderChordGrid(analysis);
  await renderGuitarChords(analysis);
  await renderCurrentNextChordDiagrams(analysis);
  dom.rawOutput.textContent = JSON.stringify(analysis.raw, null, 2);
  dom.resultsPanel.classList.remove("hidden");
  if (dom.chordSidebar) {
    dom.chordSidebar.classList.remove("hidden");
    pinChordSidebar();
  }
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
  if (dom.chordSidebar) {
    dom.chordSidebar.classList.add("hidden");
  }
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

function setupCapoSelector() {
  if (!dom.capoSelector) return;
  dom.capoSelector.addEventListener("change", () => {
    state.capo = parseInt(dom.capoSelector.value, 10) || 0;
    if (state.analysis) {
      renderChordGrid(state.analysis);
      void renderGuitarChords(state.analysis);
      void renderCurrentNextChordDiagrams(state.analysis);
      syncPlaybackState(state.analysis);
    }
  });
}

function setupRefineHighlightToggle() {
  const btn = dom.refineResultToggle;
  if (!btn) return;
  btn.addEventListener("click", () => {
    if (btn.disabled) return;
    const refinedSet = getRefinedGridVisualIndexSet(state.analysis);
    if (refinedSet.size === 0) {
      const hint =
        (state.analysis &&
          typeof state.analysis.refineUserHint === "string" &&
          state.analysis.refineUserHint.trim()) ||
        "沒有任何拍通過 Chord Refiner。請開啟 Raw Data → chordRefine 查看各筆 skipReason。";
      window.alert(hint);
      return;
    }
    state.refineHighlightActive = !state.refineHighlightActive;
    btn.setAttribute("aria-pressed", state.refineHighlightActive ? "true" : "false");
    btn.classList.toggle("active", state.refineHighlightActive);
    if (state.analysis) {
      renderChordGrid(state.analysis);
      syncPlaybackState(state.analysis);
    }
  });
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
setupChordGridSeek();
setupCapoSelector();
setupRefineHighlightToggle();
setupChordSidebarCollapse();
updateSelectedSource();
pinChordSidebar();
loadEngineStatus();

window.addEventListener("resize", () => {
  if (state.analysis) {
    updateChordPlayhead(state.analysis);
  }
});
