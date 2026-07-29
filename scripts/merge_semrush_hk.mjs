import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { Workbook } from "@oai/artifact-tool";

const args = process.argv.slice(2);
const getArg = (name) => {
  const index = args.indexOf(name);
  return index >= 0 ? args[index + 1] : undefined;
};

const downloadsDir = getArg("--downloads-dir");
const outputPath = getArg("--output");
const previewPath = getArg("--preview");

if (!downloadsDir || !outputPath) {
  throw new Error("Usage: merge_semrush_hk.mjs --downloads-dir DIR --output FILE [--preview FILE]");
}

const relevantName = /(?:漏尿|尿滲|陰道鬆弛|陰道乾澀|私密美白|小陰唇|女性高潮|盆底肌|私密緊緻|性交疼痛).*_hk_2026-07-27.*\.csv$/u;
const entries = await fs.readdir(downloadsDir, { withFileTypes: true });
const sourceFiles = entries
  .filter((entry) => entry.isFile() && relevantName.test(entry.name))
  .map((entry) => entry.name)
  .sort((a, b) => a.localeCompare(b, "zh-Hant"));

if (sourceFiles.length === 0) {
  throw new Error("No matching Hong Kong Semrush CSV files were found.");
}

const canonicalHeaders = [
  "Source File",
  "Source Mode",
  "Groups",
  "Keyword",
  "Intent",
  "Volume",
  "Trend",
  "Keyword Difficulty",
  "CPC (USD)",
  "Competitive Density",
  "SERP Features",
  "Number of Results",
];

const rows = [];
const sourceSummaries = [];

for (const fileName of sourceFiles) {
  const fullPath = path.join(downloadsDir, fileName);
  const csvText = await fs.readFile(fullPath, "utf8");
  const workbook = await Workbook.fromCSV(csvText, { sheetName: "Imported" });
  const sheet = workbook.worksheets.getItemAt(0);
  const used = sheet.getUsedRange(true);
  const values = used?.values ?? [];
  if (values.length < 2) {
    sourceSummaries.push({ file: fileName, rows: 0, status: "header_only_or_empty" });
    continue;
  }

  const headers = values[0].map((value) => String(value ?? "").trim());
  const modeMatch = fileName.match(/_(all-keywords|broad-match|phrase-match|bulk)_/u);
  const sourceMode = modeMatch?.[1] ?? "unknown";
  let importedRows = 0;

  for (const inputRow of values.slice(1)) {
    const source = Object.fromEntries(
      headers.map((header, index) => [header, inputRow[index] ?? ""]),
    );
    if (!String(source.Keyword ?? "").trim()) continue;
    rows.push([
      fileName,
      sourceMode,
      source.Groups ?? "",
      source.Keyword ?? "",
      source.Intent ?? "",
      source.Volume ?? "",
      source.Trend ?? "",
      source["Keyword Difficulty"] ?? "",
      source["CPC (USD)"] ?? "",
      source["Competitive Density"] ?? "",
      source["SERP Features"] ?? "",
      source["Number of Results"] ?? "",
    ]);
    importedRows += 1;
  }
  sourceSummaries.push({ file: fileName, rows: importedRows, status: "imported" });
}

const escapeCsv = (value) => {
  const text = value === null || value === undefined ? "" : String(value);
  return /[",\r\n]/u.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
};

const csvLines = [
  canonicalHeaders.map(escapeCsv).join(","),
  ...rows.map((row) => row.map(escapeCsv).join(",")),
];

await fs.mkdir(path.dirname(outputPath), { recursive: true });
await fs.writeFile(outputPath, `\uFEFF${csvLines.join("\r\n")}\r\n`, "utf8");

const verificationText = await fs.readFile(outputPath, "utf8");
const finalWorkbook = await Workbook.fromCSV(verificationText, { sheetName: "Semrush" });
const verifySheet = finalWorkbook.worksheets.getItemAt(0);
verifySheet.freezePanes.freezeRows(1);
verifySheet.showGridLines = false;
const used = verifySheet.getUsedRange(true);
used.format.wrapText = false;
used.format.font = { name: "Aptos", size: 10 };
verifySheet.getRange(`A1:L1`).format = {
  fill: "#245B78",
  font: { bold: true, color: "#FFFFFF", name: "Aptos", size: 10 },
  rowHeight: 26,
};
verifySheet.getRange(`A1:L${Math.max(2, rows.length + 1)}`).format.borders = {
  insideHorizontal: { style: "thin", color: "#D9E2E8" },
};
const lastRow = Math.max(2, rows.length + 1);
verifySheet.getRange(`A1:A${lastRow}`).format.columnWidth = 42;
verifySheet.getRange(`B1:C${lastRow}`).format.columnWidth = 16;
verifySheet.getRange(`D1:D${lastRow}`).format.columnWidth = 30;
verifySheet.getRange(`E1:L${lastRow}`).format.columnWidth = 18;

const inspect = await finalWorkbook.inspect({
  kind: "table",
  range: `Semrush!A1:L${Math.min(rows.length + 1, 12)}`,
  include: "values",
  tableMaxRows: 12,
  tableMaxCols: 12,
  maxChars: 5000,
});

if (previewPath) {
  const preview = await finalWorkbook.render({
    sheetName: "Semrush",
    range: `A1:L${Math.min(rows.length + 1, 30)}`,
    scale: 1,
    format: "png",
  });
  await fs.mkdir(path.dirname(previewPath), { recursive: true });
  await fs.writeFile(previewPath, new Uint8Array(await preview.arrayBuffer()));
}

console.log(
  JSON.stringify(
    {
      output: path.resolve(outputPath),
      preview: previewPath ? path.resolve(previewPath) : null,
      sourceFiles: sourceFiles.length,
      rows: rows.length,
      columns: canonicalHeaders.length,
      sourceSummaries,
      inspect: inspect.ndjson,
    },
    null,
    2,
  ),
);
process.exit(0);
