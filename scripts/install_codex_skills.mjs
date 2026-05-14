#!/usr/bin/env node
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const repoRoot = path.resolve(path.dirname(__filename), "..");
const skillsRoot = path.join(repoRoot, "skills");

const args = new Set(process.argv.slice(2));
const force = args.has("--force");
const listOnly = args.has("--list");
const destIndex = process.argv.indexOf("--dest");
const destRoot = path.resolve(
  destIndex >= 0
    ? process.argv[destIndex + 1]
    : process.env.CODEX_HOME
      ? path.join(process.env.CODEX_HOME, "skills")
      : path.join(os.homedir(), ".codex", "skills"),
);

if (args.has("--help") || args.has("-h")) {
  console.log(`Usage: node scripts/install_codex_skills.mjs [--dest <skills-dir>] [--force] [--list]

Installs Agentic SWMM skills into the Codex skills directory.

Default destination:
  $CODEX_HOME/skills when CODEX_HOME is set
  ~/.codex/skills otherwise

Options:
  --dest <dir>  Install into a custom skills directory
  --force       Replace existing Agentic SWMM skill directories
  --list        Print skills that would be installed without copying
`);
  process.exit(0);
}

const skillDirs = fs.readdirSync(skillsRoot, { withFileTypes: true })
  .filter((entry) => entry.isDirectory())
  .map((entry) => path.join(skillsRoot, entry.name))
  .filter((dir) => fs.existsSync(path.join(dir, "SKILL.md")))
  .sort((a, b) => path.basename(a).localeCompare(path.basename(b)));

if (listOnly) {
  for (const dir of skillDirs) {
    console.log(path.basename(dir));
  }
  process.exit(0);
}

fs.mkdirSync(destRoot, { recursive: true });

const installed = [];
for (const src of skillDirs) {
  const name = path.basename(src);
  const dest = path.join(destRoot, name);
  if (fs.existsSync(dest)) {
    if (!force) {
      throw new Error(`Destination already exists: ${dest}\nUse --force to replace Agentic SWMM skills.`);
    }
    fs.rmSync(dest, { recursive: true, force: true });
  }
  copyDir(src, dest);
  installed.push({ name, dest });
}

console.log(JSON.stringify({
  ok: true,
  destination: destRoot,
  installed_count: installed.length,
  installed,
  next_steps: [
    "Restart Codex so newly installed skills are discovered.",
    "Run `node scripts/generate_mcp_configs.mjs` to generate MCP registration commands.",
    "Run `node scripts/smoke_mcp_servers.mjs` to verify MCP server discovery.",
  ],
}, null, 2));

function copyDir(src, dest) {
  fs.mkdirSync(dest, { recursive: true });
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    const srcPath = path.join(src, entry.name);
    const destPath = path.join(dest, entry.name);
    if (entry.isDirectory()) {
      copyDir(srcPath, destPath);
    } else if (entry.isFile()) {
      fs.copyFileSync(srcPath, destPath);
    }
  }
}
