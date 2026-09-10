"use strict";

const { execFileSync } = require("node:child_process");
const { existsSync } = require("node:fs");
const { join, dirname } = require("node:path");
const { parse } = require("shell-quote");

const PLATFORMS = {
  "linux-x64": "@openapi-mcp-rs/linux-x64",
  "linux-arm64": "@openapi-mcp-rs/linux-arm64",
  "darwin-x64": "@openapi-mcp-rs/darwin-x64",
  "darwin-arm64": "@openapi-mcp-rs/darwin-arm64",
  "win32-x64": "@openapi-mcp-rs/win32-x64",
};

function getPlatformPackage() {
  const key = `${process.platform}-${process.arch}`;
  return PLATFORMS[key] || null;
}

function getBinaryPath(baseDir = __dirname) {
  const pkg = getPlatformPackage();
  const ext = process.platform === "win32" ? ".exe" : "";
  const binName = `openapi-mcp${ext}`;

  // 1. Local development: check target/ directory first
  //    Try debug first, then release
  const repoRoot = join(baseDir, "../..");
  if (existsSync(join(repoRoot, "Cargo.toml"))) {
    const debugBin = join(repoRoot, "target", "debug", binName);
    if (existsSync(debugBin)) {
      return debugBin;
    }
    const releaseBin = join(repoRoot, "target", "release", binName);
    if (existsSync(releaseBin)) {
      return releaseBin;
    }
  }

  // 2. Try npm-installed package (production path)
  if (pkg) {
    try {
      const pkgJsonPath = require.resolve(`${pkg}/package.json`);
      const binPath = join(dirname(pkgJsonPath), "bin", binName);
      if (existsSync(binPath)) {
        return binPath;
      }
    } catch {
      // Package not installed via npm
    }
  }

  return null;
}

function parseCommand(commandStr) {
  const parsed = parse(commandStr);

  // shell-quote can return objects for operators like { op: '|' }
  // We only accept plain string tokens
  for (const token of parsed) {
    if (typeof token !== "string") {
      throw new Error(
        `Unsupported shell operator in command: ${JSON.stringify(token)}`
      );
    }
  }

  if (parsed.length === 0) {
    throw new Error("Command is empty after parsing");
  }

  return parsed;
}

function main() {
  let exe;
  let prefixArgs = [];

  const commandEnv = process.env.OPENAPI_MCP_NPM_COMMAND;
  if (commandEnv) {
    try {
      const parsed = parseCommand(commandEnv);
      [exe, ...prefixArgs] = parsed;
    } catch (error) {
      process.stderr.write(
        `Error: Failed to parse OPENAPI_MCP_NPM_COMMAND: ${error.message}

The OPENAPI_MCP_NPM_COMMAND environment variable should contain a shell-quoted command.
Examples:
    OPENAPI_MCP_NPM_COMMAND="/path/to/openapi-mcp"
    OPENAPI_MCP_NPM_COMMAND="node /path/to/script.js"
    OPENAPI_MCP_NPM_COMMAND='"/path with spaces/openapi-mcp"'
`
      );
      process.exit(1);
    }
  } else {
    exe = getBinaryPath();
  }

  if (!exe) {
    const platform = `${process.platform}-${process.arch}`;
    process.stderr.write(`Error: Unsupported platform: ${platform}

No pre-built binary is available for your platform.
You can build from source using Cargo:

    cargo install openapi-mcp

This requires the Rust toolchain. See https://rustup.rs for installation.
`);
    process.exit(1);
  }

  const args = [...prefixArgs, ...process.argv.slice(2)];

  try {
    execFileSync(exe, args, { stdio: "inherit" });
  } catch (error) {
    process.exit(error.status ?? 1);
  }
}

module.exports = {
  PLATFORMS,
  getPlatformPackage,
  getBinaryPath,
  parseCommand,
  main,
};
