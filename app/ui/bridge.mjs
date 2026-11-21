export class Bridge {
  constructor(win) {
    this.win = win; this.pending = new Map(); this.id = 0; this.ready = false;
    this.onresult = () => {}; this.oncontext = () => {}; this.onclose = () => {};
    this.listener = event => {
      if (event.source !== win.parent || event.data?.jsonrpc !== "2.0") return;
      const message = event.data;
      if (this.pending.has(message.id)) {
        const pending = this.pending.get(message.id); this.pending.delete(message.id); clearTimeout(pending.timer);
        if (message.error) pending.reject(new Error("Host request failed")); else pending.resolve(message.result);
      } else if (message.method === "ui/notifications/tool-result") this.onresult(message.params);
      else if (message.method === "ui/notifications/host-context-changed") this.oncontext(message.params);
      else if (message.method === "ping") this.post({ id: message.id, result: {} });
      else if (message.method === "ui/resource-teardown") {
        this.onclose(); this.post({ id: message.id, result: {} }); this.close();
      } else if (message.method === "ui/notifications/tool-cancelled") { this.onclose(); this.close(); }
      else if (message.id !== undefined && message.method) this.post({ id: message.id, error: { code: -32601, message: "Unsupported method" } });
    };
    win.addEventListener("message", this.listener);
  }
  post(value) { this.win.parent.postMessage({ jsonrpc: "2.0", ...value }, "*"); }
  request(method, params) {
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error("Host request timed out")); }, 15000);
      this.pending.set(id, { resolve, reject, timer }); this.post({ id, method, params });
    });
  }
  async connect() {
    const result = await this.request("ui/initialize", {
      protocolVersion: "2026-01-26", appInfo: { name: "Travel Scam Explorer", version: "1.0.0" },
      appCapabilities: { availableDisplayModes: ["inline", "fullscreen"] },
    });
    this.capabilities = result.hostCapabilities || {}; this.ready = true;
    this.oncontext(result.hostContext || {});
    this.post({ method: "ui/notifications/initialized", params: {} });
    return result;
  }
  async tool(name, args) {
    if (!this.ready || !this.capabilities.serverTools) throw new Error("Host tool access unavailable");
    const result = await this.request("tools/call", { name, arguments: args });
    if (result?.isError || !result?.structuredContent) {
      const error = new Error("Tool call unavailable");
      const code = result?.structuredContent?.error?.code;
      if (["invalid_request", "not_found", "conflict", "timeout", "unavailable"].includes(code)) error.code = code;
      throw error;
    }
    return result.structuredContent;
  }
  close() {
    this.ready = false; this.win.removeEventListener("message", this.listener);
    for (const item of this.pending.values()) { clearTimeout(item.timer); item.reject(new Error("View closed")); }
    this.pending.clear();
  }
}
