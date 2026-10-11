import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { existsSync } from "node:fs";
import { createServer } from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const root = path.resolve(frontend, "..");

async function assertPortFree(port: number) {
  await new Promise<void>((resolve, reject) => {
    const reservation = createServer();
    reservation.once("error", () => reject(new Error("Isolated E2E ports are occupied; choose unused port overrides")));
    reservation.listen(port, "127.0.0.1", () => reservation.close(() => resolve()));
  });
}

async function ready(url: string, child: ChildProcess, timeout = 90_000, secret?: string) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error("Isolated E2E server exited before readiness");
    try {
      const response = await fetch(url, {
        signal: AbortSignal.timeout(1500),
        headers: secret ? { "X-OfferPilot-E2E-Control": secret } : undefined,
      });
      if (response.ok && (!secret || (await response.json() as { ready?: boolean }).ready === true)) return;
    } catch { /* Server starts asynchronously. */ }
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
  throw new Error("Isolated E2E server did not become ready");
}

async function stopped(child: ChildProcess, milliseconds: number) {
  if (child.exitCode !== null) return true;
  return new Promise<boolean>((resolve) => {
    const onExit = () => { clearTimeout(timer); resolve(true); };
    const timer = setTimeout(() => { child.off("exit", onExit); resolve(false); }, milliseconds);
    child.once("exit", onExit);
  });
}

async function terminateTree(child: ChildProcess) {
  if (!child.pid || child.exitCode !== null) return;
  if (process.platform === "win32") {
    spawnSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], { stdio: "ignore", windowsHide: true });
  } else {
    child.kill("SIGTERM");
  }
  await stopped(child, 5000);
}

export default async function globalSetup() {
  const apiPort = Number(process.env.OFFERPILOT_E2E_API_PORT ?? 8001);
  const webPort = Number(process.env.OFFERPILOT_E2E_WEB_PORT ?? 3001);
  for (const port of [apiPort, webPort]) {
    if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error("Invalid isolated E2E ports");
  }
  if (apiPort === webPort) throw new Error("Isolated API and Web ports must differ");
  await assertPortFree(apiPort);
  await assertPortFree(webPort);
  const apiOrigin = `http://127.0.0.1:${apiPort}`;
  const webOrigin = `http://127.0.0.1:${webPort}`;
  const environment = {
    ...process.env,
    OFFERPILOT_E2E_API_PORT: String(apiPort),
    OFFERPILOT_E2E_WEB_PORT: String(webPort),
    OFFERPILOT_API_URL: apiOrigin,
    OFFERPILOT_WEB_ORIGINS: JSON.stringify([webOrigin]),
    NEXT_TELEMETRY_DISABLED: "1",
    HTTP_PROXY: "", HTTPS_PROXY: "", ALL_PROXY: "",
  };
  const venv = path.join(root, "backend", ".venv");
  const venvPython = process.env.OFFERPILOT_E2E_PYTHON ?? path.join(venv, process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
  if (!existsSync(venvPython)) throw new Error("Install the locked backend environment before running E2E");
  let python = venvPython;
  if (process.platform === "win32") {
    const base = spawnSync(venvPython, ["-c", "import sys; print(sys._base_executable)"], { encoding: "utf8", windowsHide: true });
    if (base.status !== 0) throw new Error("Could not resolve the backend Python interpreter");
    python = base.stdout.trim();
  }
  const pythonEnvironment = {
    ...environment,
    PYTHONPATH: [path.join(root, "backend", "src"), path.join(venv, process.platform === "win32" ? "Lib/site-packages" : "lib/python3.11/site-packages")].join(path.delimiter),
  };
  const backend = spawn(python, ["-B", "-u", path.join(frontend, "scripts", "e2e-backend.py")], {
    cwd: root, env: pythonEnvironment, stdio: ["pipe", "pipe", "ignore"], windowsHide: true,
  });
  let web: ChildProcess | undefined;
  const cleanup = async () => {
    if (web) await terminateTree(web);
    backend.stdin?.end();
    if (!(await stopped(backend, 20_000))) {
      await terminateTree(backend);
      throw new Error("Isolated E2E API needed forced shutdown; temporary cleanup is unverified");
    }
    if (backend.exitCode !== 0) throw new Error("Isolated E2E API cleanup failed");
  };
  try {
    const protocol = await new Promise<{ control_secret: string }>((resolve, reject) => {
      let buffer = "";
      const timeout = setTimeout(() => reject(new Error("Isolated E2E API setup timed out")), 30_000);
      backend.stdout?.on("data", (chunk: Buffer) => {
        buffer += chunk.toString();
        if (buffer.includes("\n")) {
          clearTimeout(timeout);
          try { resolve(JSON.parse(buffer.split("\n")[0])); }
          catch { reject(new Error("Invalid private E2E setup protocol")); }
        }
      });
      backend.once("exit", () => { clearTimeout(timeout); reject(new Error("Isolated E2E API setup failed")); });
      backend.once("error", () => { clearTimeout(timeout); reject(new Error("Could not launch isolated E2E API")); });
    });
    process.env.OFFERPILOT_E2E_CONTROL_SECRET = protocol.control_secret;
    process.env.OFFERPILOT_E2E_API_ORIGIN = apiOrigin;
    await ready(`${apiOrigin}/__e2e/ready`, backend, 20_000, protocol.control_secret);
    await assertPortFree(webPort);
    web = spawn(process.execPath, [path.join(frontend, "node_modules", "next", "dist", "bin", "next"), "dev", "--hostname", "127.0.0.1", "--port", String(webPort)], {
      cwd: frontend, env: environment, stdio: "ignore", windowsHide: true,
    });
    await ready(`${webOrigin}/login`, web);
    return cleanup;
  } catch (error) {
    await cleanup();
    throw error;
  }
}
