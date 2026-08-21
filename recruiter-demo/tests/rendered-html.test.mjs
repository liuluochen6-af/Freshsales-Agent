import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  return worker.fetch(new Request("http://localhost/", { headers: { accept: "text/html" } }), {
    ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) },
  }, { waitUntil() {}, passThroughOnException() {} });
}

test("server-renders the Freshsales-Agent recruiter demo", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);
  const html = await response.text();
  assert.match(html, /<title>Freshsales-Agent/);
  assert.match(html, /报价 Agent.*工作台/);
  assert.match(html, /8 个专职角色/);
  assert.match(html, /真实公开数据/);
  assert.match(html, /Bank of Thailand/);
  assert.match(html, /不连接真实客户系统/);
  assert.doesNotMatch(html, /DurianFlow|Your site is taking shape/);
});

test("ships interactive routing and safety cases", async () => {
  const page = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  assert.match(page, /useState/);
  assert.match(page, /routeMessage/);
  assert.match(page, /不要再联系/);
  assert.match(page, /退款.*赔偿/);
  assert.match(page, /blocked: true/);
  assert.match(page, /Freshsales-Agent Console/);
  assert.match(page, /setSelectedAgent/);
  assert.match(page, /下载 CSV/);
});
