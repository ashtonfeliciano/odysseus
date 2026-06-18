const fs = require("fs");
const { execSync } = require("child_process");

const raw = execSync('find /root/.npm/_npx -name "main.js" -path "*/google-drive-mcp/dist/*"', { encoding: "utf8" }).trim();
const mainPath = raw.split("\n").find(p => p.includes("google-drive-mcp/dist"));
if (!mainPath) { console.error("main.js not found"); process.exit(1); }

let code = fs.readFileSync(mainPath, "utf8");

if (code.includes("PKCE_STRIPPED_V2")) {
  console.log("Patch v2 already applied:", mainPath);
  process.exit(0);
}

// Patch 1: Remove code_challenge from Google authorize URL (already done in v1 if present)
code = code.replace(/^\s*code_challenge: codeChallenge,\s*$/m, "    /* PKCE_STRIPPED_V2: code_challenge */");
code = code.replace(/^\s*code_challenge_method: codeChallengeMethod,\s*$/m, "    /* PKCE_STRIPPED_V2: code_challenge_method */");
// Also clear old v1 markers in case they exist
code = code.replace(/\/\* PKCE_STRIPPED: code_challenge \*\//g, "/* PKCE_STRIPPED_V2: code_challenge */");
code = code.replace(/\/\* PKCE_STRIPPED: code_challenge_method \*\//g, "/* PKCE_STRIPPED_V2: code_challenge_method */");

// Patch 2: Strip code_verifier from the TOKEN PROXY handler only.
// Anchor on the unique comment that precedes it, then find the first ...req.body, after it.
const ANCHOR = "// Token endpoint - proxy to Google";
const anchorIdx = code.indexOf(ANCHOR);
if (anchorIdx === -1) { console.error("Could not find token endpoint anchor"); process.exit(1); }

const reqBodyStr = "...req.body,";
const reqBodyIdx = code.indexOf(reqBodyStr, anchorIdx);
if (reqBodyIdx === -1) { console.error("Could not find ...req.body, in token handler"); process.exit(1); }

const stripFilter = '...Object.fromEntries(Object.entries(req.body).filter(([k]) => k !== "code_verifier")), /* PKCE_STRIPPED_V2 */';
code = code.slice(0, reqBodyIdx) + stripFilter + code.slice(reqBodyIdx + reqBodyStr.length);

fs.writeFileSync(mainPath, code);
console.log("google-drive-mcp PKCE patch v2 applied:", mainPath);
