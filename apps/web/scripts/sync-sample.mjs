// Copies the canonical simulated sample (data/samples) into public/ so the web
// app and the API tests always use the same file. The public copy is
// generated and git-ignored; edit data/samples instead.
import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const source = resolve(here, "../../../data/samples/lagos_pilot_simulated.csv");
const target = resolve(here, "../public/data/lagos_pilot_simulated.csv");

mkdirSync(dirname(target), { recursive: true });
copyFileSync(source, target);
console.log(`Synced sample dataset -> ${target}`);
