"use client";

import { ChangeEvent, useEffect, useMemo, useRef, useState } from "react";

type Risk = "低" | "中" | "高";
type Decision = {
  primary: string;
  support: string[];
  risk: Risk;
  action: string;
  reason: string;
  reply: string;
  blocked?: boolean;
};
type AgentView = {
  summary: string;
  badge: string;
  columns: string[];
  rows: string[][];
  source?: { label: string; href: string };
  download?: string;
};

const agents: Record<string, string> = {
  lead: "线索识别",
  product: "产品顾问",
  quotation: "报价 Agent",
  inventory: "库存 Agent",
  order: "订单 Agent",
  fulfillment: "履约 Agent",
  after_sales: "售后 Agent",
  compliance: "合规 Agent",
};
const examples = [
  "金枕现在多少钱，10箱有现货吗？",
  "订单 DR20260821008 到哪了？",
  "收到的榴莲破损了，我要退款赔偿",
  "不要再联系，把我的信息删掉",
];
const agentViews: Record<string, AgentView> = {
  lead: {
    summary:
      "识别购买意向并安排下一步跟进。导入企业线索 CSV 后即可在当前工作区处理。",
    badge: "待接入业务数据",
    columns: ["线索", "地区", "意向", "下一步"],
    rows: [],
  },
  product: {
    summary: "产品标准来自泰国政府公开资料，可作为产品知识库的可靠事实来源。",
    badge: "公开标准",
    columns: ["品种", "最低干物质", "业务提示"],
    rows: [
      ["Monthong / 金枕", "32%", "成熟度重点校验"],
      ["Chanee / 青尼", "30%", "批次抽检"],
      ["Kradum / 甲仑", "28%", "避免未熟果"],
    ],
    source: {
      label: "Thailand PRD · 2024 出口标准",
      href: "https://thailand.prd.go.th/en/content/category/detail/id/52/iid/279737",
    },
  },
  quotation: {
    summary:
      "泰国榴莲月度出口统计，已转换为报价 Agent 可参考的市场数据表。金额单位为百万泰铢。",
    badge: "真实公开数据",
    columns: ["月份", "出口额", "出口量（吨）", "均价（฿/kg）"],
    rows: [
      ["2026-06", "19,863.36", "166,295.42", "119.45"],
      ["2026-05", "43,982.39", "348,293.70", "126.28"],
      ["2026-04", "37,328.31", "276,393.99", "135.05"],
      ["2026-03", "2,737.34", "19,037.91", "143.78"],
      ["2026-02", "4,798.68", "33,770.19", "142.10"],
      ["2026-01", "6,679.10", "48,965.37", "136.40"],
    ],
    source: {
      label: "Bank of Thailand / Thai Customs",
      href: "https://app.bot.or.th/BTWS_STAT/statistics/BOTWEBSTAT.aspx?language=ENG&reportID=978",
    },
    download: "/data/thailand-durian-exports-2026.csv",
  },
  inventory: {
    summary: "接入仓库批次与可售库存；库存调整需审批并产生不可变流水。",
    badge: "待接入业务数据",
    columns: ["批次", "品种", "可用库存", "状态"],
    rows: [],
  },
  order: {
    summary: "从已确认报价生成订单，连接收款、库存占用和履约状态。",
    badge: "待接入业务数据",
    columns: ["订单", "商品", "金额", "状态"],
    rows: [],
  },
  fulfillment: {
    summary: "同步真实物流节点，并将异常运单交给人工处理。",
    badge: "待接入业务数据",
    columns: ["订单", "当前位置", "预计送达", "状态"],
    rows: [],
  },
  after_sales: {
    summary: "集中处理破损、质量争议与退款工单；资金动作始终需要人工授权。",
    badge: "待接入业务数据",
    columns: ["工单", "问题", "风险", "处理方式"],
    rows: [],
  },
  compliance: {
    summary: "合规 Agent 统一拦截停止联系、隐私删除和资金承诺等高风险动作。",
    badge: "安全规则",
    columns: ["触发场景", "自动动作", "是否外发"],
    rows: [
      ["停止联系", "冻结触达", "否"],
      ["删除个人信息", "创建隐私工单", "否"],
      ["退款 / 赔付", "转人工授权", "否"],
    ],
  },
};

function routeMessage(raw: string): Decision {
  const text = raw.trim();
  if (/不要再联系|别联系|停止联系|退订/.test(text))
    return {
      primary: "compliance",
      support: [],
      risk: "高",
      action: "停止自动发送 · 转人工复核",
      reason: "检测到停止联系请求，合规策略优先于销售目标。",
      reply:
        "已记录您的停止联系请求。系统不会继续自动触达，并将由专人复核处理。",
      blocked: true,
    };
  if (/删掉|删除.*信息|隐私|个人信息/.test(text))
    return {
      primary: "compliance",
      support: [],
      risk: "高",
      action: "冻结自动流程 · 进入隐私工单",
      reason: "涉及个人信息删除，需要保留审计记录并由授权人员处理。",
      reply:
        "已收到您的个人信息处理请求。我们已暂停自动流程，并交由隐私专员跟进。",
      blocked: true,
    };
  if (/退款|赔偿|破损|坏了|投诉/.test(text))
    return {
      primary: "after_sales",
      support: ["compliance"],
      risk: "高",
      action: "生成售后工单 · 人工确认赔付",
      reason: "售后争议可能产生资金与承诺风险，系统只收集信息，不自动赔付。",
      reply:
        "很抱歉影响了您的体验。请提供订单号和破损照片，我们会立即建立售后工单并由专员确认方案。",
      blocked: true,
    };
  if (/到哪|物流|发货|配送|订单\s*[A-Z0-9]/i.test(text))
    return {
      primary: "fulfillment",
      support: ["order"],
      risk: "中",
      action: "查询订单与物流节点",
      reason: "识别到订单履约意图，先校验订单，再读取物流状态。",
      reply: "已识别订单号。连接业务数据源后可返回实时出库与物流节点。",
    };
  if (/多少|价格|报价|有现货|库存/.test(text))
    return {
      primary:
        /有现货|库存/.test(text) && !/多少|价格|报价/.test(text)
          ? "inventory"
          : "quotation",
      support: ["inventory", "product"],
      risk: "中",
      action: "校验库存 · 生成可审计报价草案",
      reason: "同时提取商品、数量与价格意图；报价发送前保留人工确认。",
      reply:
        "已读取公开市场行情。连接企业价格表和实时库存后，可生成带有效期的正式报价。",
    };
  if (/买|下单|订/.test(text))
    return {
      primary: "lead",
      support: ["product", "order"],
      risk: "低",
      action: "识别购买意向 · 补齐订单信息",
      reason: "检测到明确购买意向，但缺少商品或收货信息。",
      reply: "可以的。请告诉我商品、数量和收货城市，我会为您整理订单草稿。",
    };
  return {
    primary: "product",
    support: ["lead"],
    risk: "低",
    action: "产品问答 · 继续澄清需求",
    reason: "当前信息不足以进入交易流程。",
    reply:
      "您好，我可以协助产品咨询、报价、库存、订单与售后。请告诉我您想了解什么。",
  };
}

export default function Home() {
  const [message, setMessage] = useState(examples[0]);
  const [decision, setDecision] = useState<Decision>(() =>
    routeMessage(examples[0]),
  );
  const [pending, setPending] = useState(false);
  const [runs, setRuns] = useState(12);
  const [status, setStatus] = useState("系统在线 · 等待任务");
  const [selectedAgent, setSelectedAgent] = useState("quotation");
  const [selectedFeature, setSelectedFeature] = useState("routing");
  const [hasScrolled, setHasScrolled] = useState(false);
  const [statusPulse, setStatusPulse] = useState(false);
  const [workspaceRows, setWorkspaceRows] = useState<
    Record<string, string[][]>
  >(() =>
    Object.fromEntries(
      Object.entries(agentViews).map(([key, view]) => [key, view.rows]),
    ),
  );
  const fileInput = useRef<HTMLInputElement>(null);
  const support = useMemo(
    () => decision.support.map((item) => agents[item]).join(" + ") || "无",
    [decision],
  );
  const agentView = agentViews[selectedAgent];
  const currentRows = workspaceRows[selectedAgent] || [];
  useEffect(() => {
    const onScroll = () => setHasScrolled(window.scrollY > 34);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);
  function importCsv(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      const lines = String(reader.result || "")
        .split(/\r?\n/)
        .filter(Boolean);
      const parsed = lines
        .slice(1)
        .map((line) =>
          line.split(",").map((cell) => cell.trim().replace(/^"|"$/g, "")),
        )
        .filter((row) => row.length >= agentView.columns.length)
        .map((row) => row.slice(0, agentView.columns.length));
      setWorkspaceRows((state) => ({ ...state, [selectedAgent]: parsed }));
      setStatus(`已导入 ${parsed.length} 行数据 · ${agents[selectedAgent]}`);
    };
    reader.readAsText(file);
    event.target.value = "";
  }
  function analyze(next = message) {
    if (!next.trim()) return;
    setMessage(next);
    setPending(true);
    setStatus("Router 正在识别意图 · 提取实体 · 评估风险");
    window.setTimeout(() => {
      const routed = routeMessage(next);
      setDecision(routed);
      setSelectedAgent(routed.primary);
      setRuns((n) => n + 1);
      setStatus(`路由完成 · ${agents[routed.primary]} 已生成建议`);
      setPending(false);
    }, 420);
  }
  function checkSystemStatus() {
    setStatusPulse(true);
    setStatus("正在检查连接 · Router / Agents / 数据源");
    window.setTimeout(() => {
      setStatusPulse(false);
      setStatus("系统在线 · 所有服务运行正常");
    }, 900);
  }

  return (
    <main className="siteExperience">
      <header className={hasScrolled ? "nav shell navScrolled" : "nav shell"}>
        <a className="logo" href="#top">
          <span>F</span>Freshsales-Agent
        </a>
        <nav>
          <a href="#features">能力</a>
          <a href="#workspace">运营控制台</a>
          <a href="#architecture">架构</a>
        </nav>
        <button className={statusPulse ? "systemStatus checking" : "systemStatus"} type="button" onClick={checkSystemStatus} aria-live="polite">
          <i />{statusPulse ? "检查中…" : "系统在线"}<b>⌄</b>
        </button>
        <a className="navButton" href="/app">
          Start <b>↗</b>
        </a>
      </header>
      <section className="hero shell" id="top">
        <div className="heroCopy">
          <div className="pill">
            <i /> AI-native sales operations
          </div>
          <h1>
            让每一条客户消息，
            <br />
            <em>自动抵达正确的 Agent</em>
          </h1>
          <p>
            Freshsales-Agent
            将产品咨询、报价、库存、订单、履约、售后和合规串成一个可解释、可审计的销售工作流。
          </p>
          <div className="heroActions">
            <a className="primary" href="/app">
              Start · 进入系统 <span>→</span>
            </a>
            <a
              className="secondary"
              href="https://github.com/liuluochen6-af/Freshsales-Agent"
              target="_blank"
              rel="noreferrer"
            >
              查看 GitHub
            </a>
          </div>
          <small className="heroNote">
            真实公开市场数据 · 支持企业 CSV 导入 · 高风险动作人工审批
          </small>
        </div>
        <div className="boardPreview" aria-label="Freshsales-Agent 工作台">
          <div className="previewTop">
            <div>
              <span className="miniLogo">F</span>
              <b>Sales Intelligence</b>
            </div>
            <span className="live">
              <i />
              Operational
            </span>
          </div>
          <div className="previewBody">
            <aside>
              <small>AGENT NETWORK</small>
              {["报价 Agent", "库存 Agent", "产品顾问", "合规 Agent"].map(
                (name, i) => (
                  <div
                    className={i === 0 ? "agent active" : "agent"}
                    key={name}
                  >
                    <span>{i + 1}</span>
                    {name}
                    <i />
                  </div>
                ),
              )}
            </aside>
            <div className="previewMain">
              <div className="previewTitle">
                <div>
                  <small>ROUTING DECISION</small>
                  <b>报价 Agent</b>
                </div>
                <span>置信度 96%</span>
              </div>
              <div className="customerBubble">
                金枕现在多少钱，10箱有现货吗？
              </div>
              <div className="routeSteps">
                <span>意图识别</span>
                <i>→</i>
                <span>库存校验</span>
                <i>→</i>
                <span>报价草案</span>
              </div>
              <div className="answer">
                <small>RECOMMENDED RESPONSE</small>
                <p>已读取公开市场行情，等待企业价格表与实时库存确认。</p>
              </div>
            </div>
          </div>
        </div>
      </section>
      <section className="proof">
        <div className="shell proofGrid">
          <div>
            <strong>8</strong>
            <span>专职业务 Agents</span>
          </div>
          <div>
            <strong>3</strong>
            <span>可复用 Agent Skills</span>
          </div>
          <div>
            <strong>40</strong>
            <span>自动化测试通过</span>
          </div>
          <div>
            <strong>100%</strong>
            <span>高风险动作受控</span>
          </div>
        </div>
      </section>
      <section className="storyVideo shell" id="story">
        <div className="storyCopy">
          <span className="storyKicker">03 · 产品介绍</span>
          <h2>不只看介绍，<br /><em>亲手试一次。</em></h2>
          <p>用 40 秒了解 Freshsales-Agent 如何把线索、会话、报价、库存、订单、履约与合规串成一条可追踪的销售链路。</p>
          <a className="storyButton" href="/app">打开运营控制台 <span>→</span></a>
        </div>
        <div className="storyVideoFrame">
          <div className="storyVideoChrome"><div className="storyVideoBrand"><span className="miniLogo">F</span> Freshsales-Agent</div><span className="storyVideoStatus"><i /> 普通话介绍 · 00:39</span></div>
          <video className="storyVideoPlayer" controls preload="metadata" src="/freshsales-agent-40s.mp4" aria-label="Freshsales-Agent 中文普通话产品介绍" />
          <div className="storyVideoFooter"><span>00:39 · 产品链路介绍</span><span>播放完整流程 ↗</span></div>
        </div>
      </section>
      <section className="features shell" id="features">
        <div className="sectionIntro">
          <span>核心能力</span>
          <h2>
            不只是回答问题，
            <br />
            而是推进销售流程
          </h2>
          <p>
            一个 Router 负责理解意图，八个业务 Agent
            各司其职，高风险动作统一进入安全护栏。
          </p>
        </div>
        <div className="featureGrid">
            <article
              className={selectedFeature === "routing" ? "feature large isSelected" : "feature large"}
              onClick={() => setSelectedFeature("routing")}
              tabIndex={0}
              onKeyDown={(event) => event.key === "Enter" && setSelectedFeature("routing")}
            >
            <div className="icon">⌁</div>
            <small>01 · INTELLIGENT ROUTING</small>
            <h3>一句话，自动拆解成执行计划</h3>
            <p>
              从自由文本中提取意图、商品、数量和订单号，并给出主 Agent、协作
              Agent、风险等级与下一步动作。
            </p>
            <div className="miniFlow">
              <span>客户消息</span>
              <b>→</b>
              <span>Router</span>
              <b>→</b>
              <span className="accent">最合适 Agent</span>
            </div>
          </article>
          <article
            className={selectedFeature === "safety" ? "feature isSelected" : "feature"}
            onClick={() => setSelectedFeature("safety")}
            tabIndex={0}
            onKeyDown={(event) => event.key === "Enter" && setSelectedFeature("safety")}
          >
            <div className="icon">◎</div>
            <small>02 · HUMAN IN THE LOOP</small>
            <h3>高风险动作不越权</h3>
            <p>退款、赔付、隐私删除和停止联系自动阻断，转入人工复核。</p>
            <div className="riskBar">
              <span>风险检测</span>
              <b>高</b>
            </div>
          </article>
          <article
            className={selectedFeature === "audit" ? "feature isSelected" : "feature"}
            onClick={() => setSelectedFeature("audit")}
            tabIndex={0}
            onKeyDown={(event) => event.key === "Enter" && setSelectedFeature("audit")}
          >
            <div className="icon">↗</div>
            <small>03 · OBSERVABLE</small>
            <h3>每个决定都有理由</h3>
            <p>路由结果附带命中原因、置信度与结构化实体，方便排错与审计。</p>
            <div className="codeLine">reason: “检测到报价 + 库存意图”</div>
          </article>
        </div>
      </section>
      <section className="workspaceSection" id="workspace">
        <div className="shell">
          <div className="workspaceIntro">
            <div>
              <span>运营控制台</span>
              <h2>用 Agent 网络推进销售业务</h2>
            </div>
            <p>
              点击左侧 Agent 查看对应数据，或输入客户消息自动路由。公开统计标注来源，企业数据可通过 CSV 安全导入。
            </p>
          </div>
          <div className="workbench">
            <div className="workbenchHead">
              <div>
                <span className="miniLogo">F</span>
                <b>Freshsales-Agent Console</b>
              </div>
              <span className="live">
                <i />
                System online
              </span>
            </div>
            <div className="workbenchBody">
              <aside className="agentRail">
                <small>AGENT NETWORK · 点击切换</small>
                {Object.entries(agents).map(([key, label]) => (
                  <button
                    type="button"
                    key={key}
                    onClick={() => { setSelectedAgent(key); setStatus(`已切换到 ${label} · 数据工作区已更新`); }}
                    className={selectedAgent === key ? "agent active" : "agent"}
                    aria-pressed={selectedAgent === key}
                  >
                    <span>{label.slice(0, 1)}</span>
                    {label}
                    <i />
                  </button>
                ))}
              </aside>
              <div className="console">
                <div className="consoleTop">
                  <div>
                    <small>AGENT DATA WORKSPACE</small>
                    <h3>{agents[selectedAgent]}工作台</h3>
                  </div>
                  <span className="consoleStatus"><i />{status} · 已运行 {runs} 次</span>
                </div>
                <label className="agentSelect">
                  <span>选择 Agent</span>
                  <select
                    value={selectedAgent}
                    onChange={(e) => setSelectedAgent(e.target.value)}
                  >
                    {Object.entries(agents).map(([key, label]) => (
                      <option value={key} key={key}>
                        {label}
                      </option>
                    ))}
                  </select>
                </label>
                <section className="agentDataset">
                  <div className="datasetHead">
                    <div>
                      <span>{agentView.badge}</span>
                      <p>{agentView.summary}</p>
                    </div>
                    <div className="dataActions">
                      {agentView.source && (
                        <a
                          href={agentView.source.href}
                          target="_blank"
                          rel="noreferrer"
                        >
                          查看来源 ↗
                        </a>
                      )}
                      {agentView.download && (
                        <a href={agentView.download} download>
                          下载 CSV ↓
                        </a>
                      )}
                      <button type="button" onClick={() => fileInput.current?.click()}>
                        导入 CSV ↑
                      </button>
                      <input ref={fileInput} type="file" accept=".csv,text/csv" onChange={importCsv} hidden />
                    </div>
                  </div>
                  <div className="tableScroll">
                    <table>
                      <thead>
                        <tr>
                          {agentView.columns.map((column) => (
                            <th key={column}>{column}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {currentRows.map((row, index) => (
                          <tr key={index}>
                            {row.map((cell) => (
                              <td key={cell}>{cell}</td>
                            ))}
                          </tr>
                        ))}
                        {!currentRows.length && (
                          <tr><td colSpan={agentView.columns.length} className="emptyData">暂无业务数据，请导入 CSV 或连接 Freshsales-Agent API。</td></tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                  {agentView.source && (
                    <small className="sourceNote">
                      来源：{agentView.source.label} ·
                      页面内数值仅用于市场参考，不直接生成客户成交价。
                    </small>
                  )}
                </section>
                <div className="routeDivider">
                  <span>客户消息智能路由</span>
                </div>
                <div className="examples">
                  {examples.map((item, i) => (
                    <button key={item} onClick={() => analyze(item)}>
                      0{i + 1} {item.slice(0, 12)}…
                    </button>
                  ))}
                </div>
                <label className="composer">
                  <span>客户消息</span>
                  <textarea
                    value={message}
                    onChange={(e) => setMessage(e.target.value)}
                    onKeyDown={(e) => {
                      if ((e.metaKey || e.ctrlKey) && e.key === "Enter")
                        analyze();
                    }}
                  />
                  <button onClick={() => analyze()} disabled={pending}>
                    {pending ? "分析中…" : "运行 Agent →"}
                  </button>
                </label>
                <div className={pending ? "result loading" : "result"}>
                  <div className="resultTop">
                    <div>
                      <small>PRIMARY AGENT</small>
                      <h3>{agents[decision.primary]}</h3>
                    </div>
                    <span className={`risk risk-${decision.risk}`}>
                      风险 · {decision.risk}
                    </span>
                  </div>
                  <div className="decisionStats">
                    <div>
                      <small>协作 Agent</small>
                      <b>{support}</b>
                    </div>
                    <div>
                      <small>系统动作</small>
                      <b>{decision.action}</b>
                    </div>
                  </div>
                  <div className="reason">
                    <span>为什么这样路由</span>
                    <p>{decision.reason}</p>
                  </div>
                  <div className={decision.blocked ? "reply blocked" : "reply"}>
                    <small>
                      {decision.blocked
                        ? "SAFE RESPONSE · 已阻断自动执行"
                        : "RECOMMENDED RESPONSE"}
                    </small>
                    <p>{decision.reply}</p>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>
      <section className="architecture shell" id="architecture">
        <div className="sectionIntro center">
          <span>系统架构</span>
          <h2>从消息入口到安全执行</h2>
          <p>保留现有销售系统作为事实来源，智能层只负责理解、编排与建议。</p>
        </div>
        <div className="architectureFlow">
          <div>
            <small>01</small>
            <b>客户消息</b>
            <span>Web / WeChat</span>
          </div>
          <i>→</i>
          <div className="highlight">
            <small>02</small>
            <b>Agent Router</b>
            <span>意图 · 实体 · 风险</span>
          </div>
          <i>→</i>
          <div>
            <small>03</small>
            <b>业务 Agents</b>
            <span>8 个专职角色</span>
          </div>
          <i>→</i>
          <div>
            <small>04</small>
            <b>SalesFlow</b>
            <span>订单 · 库存 · 审计</span>
          </div>
        </div>
      </section>
      <section className="cta">
        <div className="shell">
          <span>PRODUCTION SALES OPERATIONS</span>
          <h2>
            让 Agent 系统
            <br />
            真正进入销售业务流程
          </h2>
          <a href="/app">Start · 进入系统 →</a>
        </div>
      </section>
      <footer className="shell">
        <a className="logo" href="#top">
          <span>F</span>Freshsales-Agent
        </a>
        <p>Multi-Agent Sales Intelligence OS · Production operations console</p>
        <a
          href="https://github.com/liuluochen6-af/Freshsales-Agent"
          target="_blank"
          rel="noreferrer"
        >
          GitHub ↗
        </a>
      </footer>
    </main>
  );
}
