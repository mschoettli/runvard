#!/usr/bin/env node
import fs from "node:fs";
import vm from "node:vm";

const root = new URL("../", import.meta.url);
const indexSource = fs.readFileSync(new URL("static/index.html", root), "utf8");
const loginSource = fs.readFileSync(new URL("static/login.html", root), "utf8");
const locales = ["de", "en", "fr", "it", "es", "pt"];
const translatedLocales = ["de", "fr", "it", "es", "pt"];
const failures = [];

function initializer(source, name, optional = false) {
  const marker = `const ${name}=`;
  const start = source.indexOf(marker);
  if (start < 0) {
    if (optional) return null;
    throw new Error(`missing ${name}`);
  }
  let index = start + marker.length;
  while (/\s/.test(source[index])) index += 1;
  const open = source[index];
  const close = open === "{" ? "}" : open === "[" ? "]" : null;
  if (!close) throw new Error(`unsupported initializer for ${name}`);
  let depth = 0;
  let quote = "";
  let escaped = false;
  let lineComment = false;
  let blockComment = false;
  for (let cursor = index; cursor < source.length; cursor += 1) {
    const char = source[cursor];
    const next = source[cursor + 1];
    if (lineComment) {
      if (char === "\n") lineComment = false;
      continue;
    }
    if (blockComment) {
      if (char === "*" && next === "/") {
        blockComment = false;
        cursor += 1;
      }
      continue;
    }
    if (quote) {
      if (escaped) escaped = false;
      else if (char === "\\") escaped = true;
      else if (char === quote) quote = "";
      continue;
    }
    if (char === "/" && next === "/") {
      lineComment = true;
      cursor += 1;
      continue;
    }
    if (char === "/" && next === "*") {
      blockComment = true;
      cursor += 1;
      continue;
    }
    if (char === "'" || char === '"' || char === "`") {
      quote = char;
      continue;
    }
    if (char === open) depth += 1;
    if (char === close && --depth === 0) return source.slice(index, cursor + 1);
  }
  throw new Error(`unterminated initializer for ${name}`);
}

function evaluate(source, name, context = {}, optional = false) {
  const expression = initializer(source, name, optional);
  if (expression === null) return null;
  const value = vm.runInNewContext(`(${expression})`, { RegExp, ...context });
  context[name] = value;
  return value;
}

function mergeLocales(target, additions) {
  if (!additions) return;
  for (const [locale, values] of Object.entries(additions)) {
    target[locale] = Object.assign(target[locale] || {}, values);
  }
}

function placeholderTokens(value) {
  return [...String(value ?? "").matchAll(
    /\$\d+|\{\{?[A-Za-z_][A-Za-z0-9_.-]*\}?\}|%(?:\d+\$)?[sdif]/g,
  )].map((match) => match[0]).sort();
}

function requirePlaceholderParity(name, baselineValue, translatedValue) {
  const baseline = placeholderTokens(baselineValue);
  const translated = placeholderTokens(translatedValue);
  if (baseline.join("\u0000") !== translated.join("\u0000")) {
    failures.push(
      `${name} placeholders differ: expected [${baseline.join(", ")}], got [${translated.join(", ")}]`,
    );
  }
}

function requireKeyParity(name, maps, baseline, checkedLocales = translatedLocales) {
  const expected = Object.keys(maps[baseline] || {});
  for (const locale of checkedLocales) {
    if (!maps[locale]) {
      failures.push(`${name}.${locale} is missing`);
      continue;
    }
    const missing = expected.filter((key) => !(key in maps[locale]));
    if (missing.length) failures.push(`${name}.${locale} missing: ${missing.join(" | ")}`);
    for (const key of expected.filter((item) => item in (maps[locale] || {}))) {
      requirePlaceholderParity(
        `${name}.${locale}.${key}`,
        maps[baseline][key],
        maps[locale][key],
      );
    }
  }
}

function requireLocalizedLeaves(name, value, path = name) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return;
  const present = locales.filter((locale) => locale in value);
  if (present.length) {
    const missing = locales.filter((locale) => !(locale in value));
    if (missing.length) failures.push(`${path} missing locales: ${missing.join(", ")}`);
    const baseline = "en" in value ? "en" : present[0];
    for (const locale of present.filter((item) => item !== baseline)) {
      requirePlaceholderParity(`${path}.${locale}`, value[baseline], value[locale]);
    }
    return;
  }
  for (const [key, child] of Object.entries(value)) {
    requireLocalizedLeaves(name, child, `${path}.${key}`);
  }
}

function requireExpectedTranslations(name, values, expected) {
  for (const [key, translation] of Object.entries(expected)) {
    if (values?.[key] !== translation) {
      failures.push(`${name}.${key} should be "${translation}" (got "${values?.[key] ?? "missing"}")`);
    }
  }
}

function localizedMap(values, locale) {
  return Object.fromEntries(
    Object.entries(values || {}).map(([key, translations]) => [key, translations?.[locale]]),
  );
}

const context = {};
const i18nBase = evaluate(indexSource, "I18N_BASE", context);
const i18n = evaluate(indexSource, "I18N", context);
mergeLocales(i18n, evaluate(indexSource, "I18N_COMPLETIONS", context, true));
requireKeyParity("I18N", i18n, "en");

const uiPhrases = evaluate(indexSource, "UI_PHRASES", context);
mergeLocales(uiPhrases, evaluate(indexSource, "UI_PHRASE_COMPLETIONS", context, true));
requireKeyParity("UI_PHRASES", uiPhrases, "de", ["fr", "it", "es", "pt"]);

for (const name of ["STORAGE_PHRASES", "TERMINAL_PHRASES", "BACKUP_PHRASES"]) {
  requireKeyParity(name, evaluate(indexSource, name, context), "de");
}
const vmPhrases = evaluate(indexSource, "VM_PHRASES", context);
mergeLocales(vmPhrases, evaluate(indexSource, "VM_PHRASE_COMPLETIONS", context, true));
requireKeyParity("VM_PHRASES", vmPhrases, "de");

const fedI18n = evaluate(indexSource, "FED_I18N", context);
mergeLocales(fedI18n, evaluate(indexSource, "FED_FORM_I18N", context, true));
mergeLocales(fedI18n, evaluate(indexSource, "EXTERNAL_SERVER_I18N", context, true));
requireKeyParity("FED_I18N", fedI18n, "en", locales);
for (const key of [
  "displayName", "internalUrl", "browserUrl", "allowedNetworks",
  "existingInternalUrl", "internalUrlHint", "browserUrlHint", "copy", "copied", "disk",
]) {
  for (const locale of locales) {
    if (!fedI18n[locale]?.[key]) failures.push(`FED_I18N.${locale}.${key} is missing`);
  }
}

for (const name of [
  "TOAST_PHRASES", "PROGRESS_PHRASES", "FILE_I18N", "APP_UI",
  "APP_CATEGORY_LABELS", "APP_INSTALL_STEP_PHRASES",
]) {
  requireLocalizedLeaves(name, evaluate(indexSource, name, context));
}
for (const name of ["PHRASE_PATTERNS", "TOAST_PATTERNS", "PROGRESS_PATTERNS"]) {
  evaluate(indexSource, name, context).forEach((pattern, index) => {
    const missing = translatedLocales.filter((locale) => !(locale in (pattern.t || {})));
    if (missing.length) failures.push(`${name}[${index}] missing: ${missing.join(", ")}`);
    const baseline = pattern.t?.en ?? pattern.t?.de ?? "";
    for (const locale of locales.filter((item) => item in (pattern.t || {}))) {
      requirePlaceholderParity(`${name}[${index}].${locale}`, baseline, pattern.t[locale]);
    }
  });
}

requireExpectedTranslations("I18N.de", i18n.de, {
  tileBackup: "Sicherungen",
  tileMonitoring: "Überwachung",
  tabLogs: "Protokolle",
  tabAlerts: "Warnmeldungen",
  tabAudit: "Prüfprotokoll",
});
requireExpectedTranslations("UI_PHRASES.de", uiPhrases.de, {
  Alerts: "Warnmeldungen",
  Attention: "Achtung",
  "Attention needed": "Handlungsbedarf",
  Audit: "Prüfprotokoll",
  Backups: "Sicherungen",
  Disk: "Datenträger",
  "Disk Details": "Datenträgerdetails",
  "Errors in": "Eingehende Fehler",
  "Errors out": "Ausgehende Fehler",
  Logs: "Protokolle",
  "Mount point": "Einhängepunkt",
  Mountpoint: "Einhängepunkt",
  "SSH keys": "SSH-Schlüssel",
  "Service restarted:": "Dienst neu gestartet:",
  "Set up new disks from here. The system disk is locked so beginners do not accidentally erase runvard.":
    "Neue Laufwerke hier einrichten. Das Systemlaufwerk ist gegen versehentliches Löschen geschützt.",
  "VM restarted:": "VM neu gestartet:",
});
requireExpectedTranslations("STORAGE_PHRASES.de", context.STORAGE_PHRASES.de, {
  "Disconnect target": "Ziel trennen",
  "Find iSCSI target": "iSCSI-Ziel suchen",
  "Label (optional)": "Bezeichnung (optional)",
  Mirror: "Spiegel",
});
requireExpectedTranslations("VM_PHRASES.de", vmPhrases.de, {
  "Shut down": "Herunterfahren",
  "Shutting down": "Wird heruntergefahren",
  live: "Laufend",
  off: "Ausgeschaltet",
});
requireExpectedTranslations("EXTERNAL_SERVER_I18N.de", fedI18n.de, {
  generic: "Anderer Server / Verknüpfung",
  statusConnection: "Verbindung für Statusabfragen",
});
requireExpectedTranslations("FILE_I18N.de", localizedMap(context.FILE_I18N, "de"), {
  up: "Nach oben",
  download: "Herunterladen",
  view: "Anzeigen",
  working: "Wird verarbeitet…",
  jobStarted: "Vorgang gestartet",
});
requireExpectedTranslations("APP_UI.de", localizedMap(context.APP_UI, "de"), {
  installedFirst: "Installierte Apps zuerst",
  starting: "Wird gestartet…",
  stopping: "Wird gestoppt…",
  restarting: "Wird neu gestartet…",
});
requireExpectedTranslations(
  "TOAST_PHRASES.de",
  localizedMap(context.TOAST_PHRASES, "de"),
  { "Upload failed": "Hochladen fehlgeschlagen" },
);
requireExpectedTranslations("PROGRESS_PHRASES.de", localizedMap(context.PROGRESS_PHRASES, "de"), {
  Working: "Wird verarbeitet",
  "Upload complete": "Hochladen abgeschlossen",
  "Upload failed": "Hochladen fehlgeschlagen",
});
evaluate(indexSource, "APP_DESC_ROWS", context).forEach((row, index) => {
  if (row.length !== 7 || row.slice(1).some((value) => typeof value !== "string" || !value.trim())) {
    failures.push(`APP_DESC_ROWS[${index}] is incomplete`);
  }
});

const hardcodedGermanDates = [...indexSource.matchAll(
  /toLocale(?:String|DateString|TimeString)\(\s*['"]de-DE['"]/g,
)];
if (hardcodedGermanDates.length) failures.push(`${hardcodedGermanDates.length} hard-coded de-DE date formats`);

for (const literal of [
  "${s.cpu.cores} Kerne",
  "label:'▲ Gesendet'",
  "label:'▼ Empfangen'",
  "+' Pakete'",
  "toast('Rolle: '",
  ">Copy</button>",
  "toast('Copied')",
  "isModernUi()?modernUsedWord():'used'",
]) {
  if (indexSource.includes(literal)) failures.push(`untranslated runtime literal: ${literal}`);
}

const loginText = evaluate(loginSource, "TEXT");
requireKeyParity("login.TEXT", loginText, "en", locales);
if (!loginSource.includes("localStorage.getItem('runvard_language')")) {
  failures.push("login does not use the saved interface language");
}

if (indexSource.includes(
  "['wireguard-ui','Privatsphäre-freundliche Google-Suche'",
)) failures.push("wireguard-ui has an unrelated app description");

const trKeys = new Set();
for (const match of indexSource.matchAll(/\btr\(\s*(['"])(.*?)\1\s*\)/g)) trKeys.add(match[2]);
for (const match of indexSource.matchAll(/data-i18n(?:-title|-placeholder)?=["']([^"']+)["']/g)) {
  trKeys.add(match[1]);
}
const unknownKeys = [...trKeys].filter((key) => !(key in i18nBase));
if (unknownKeys.length) failures.push(`unknown i18n keys: ${unknownKeys.join(" | ")}`);

if (failures.length) {
  console.error(`i18n check failed (${failures.length}):`);
  failures.forEach((failure) => console.error(`- ${failure}`));
  process.exit(1);
}
console.log(`i18n check passed for ${locales.join(", ")}`);
