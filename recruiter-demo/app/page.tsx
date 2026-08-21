"use client";

import { useMemo, useState } from "react";

type Risk = "低" | "中" | "高";
type Decision = { primary: string; support: string[]; risk: Risk; action: string; reason: string; reply: string; blocked?: boolean };

const agents: Record<string, string> = {
  lead: "线索识别", product: "产品顾问", quotation: "报价 Agent", inventory: "库存 Agent",
  order: "订单 Agent", fulfillment: "履约 Agent", after_sales: "售后 Agent", compliance: "合规 Agent",
};
const examples = ["金枕现在多少钱，10箱有现货吗？", "订单 DR20260821008 到哪了？", "收到的榴莲破损了，我要退款赔偿", "不要再联系，把我的信息删掉"];

function routeMessage(raw: string): Decision {
  const text = raw.trim();
  if (/不要再联系|别联系|停止联系|退订/.test(text)) return { primary: "compliance", support: [], risk: "高", action: "停止自动发送 · 转人工复核", reason: "检测到停止联系请求，合规策略优先于销售目标。", reply: "已记录您的停止联系请求。系统不会继续自动触达，并将由专人复核处理。", blocked: true };
  if (/删掉|删除.*信息|隐私|个人信息/.test(text)) return { primary: "compliance", support: [], risk: "高", action: "冻结自动流程 · 进入隐私工单", reason: "涉及个人信息删除，需要保留审计记录并由授权人员处理。", reply: "已收到您的个人信息处理请求。我们已暂停自动流程，并交由隐私专员跟进。", blocked: true };
  if (/退款|赔偿|破损|坏了|投诉/.test(text)) return { primary: "after_sales", support: ["compliance"], risk: "高", action: "生成售后工单 · 人工确认赔付", reason: "售后争议可能产生资金与承诺风险，系统只收集信息，不自动赔付。", reply: "很抱歉影响了您的体验。请提供订单号和破损照片，我们会立即建立售后工单并由专员确认方案。", blocked: true };
  if (/到哪|物流|发货|配送|订单\s*[A-Z0-9]/i.test(text)) return { primary: "fulfillment", support: ["order"], risk: "中", action: "查询订单与物流节点", reason: "识别到订单履约意图，先校验订单，再读取物流状态。", reply: "已识别订单 DR20260821008。演示数据：订单已出库，正在运输中，预计明日送达。" };
  if (/多少|价格|报价|有现货|库存/.test(text)) return { primary: /有现货|库存/.test(text) && !/多少|价格|报价/.test(text) ? "inventory" : "quotation", support: ["inventory", "product"], risk: "中", action: "校验库存 · 生成可审计报价草案", reason: "同时提取商品、数量与价格意图；报价发送前保留人工确认。", reply: "金枕 A 果演示报价为 ¥428/箱，10 箱库存可用。报价有效期 24 小时，确认后可生成订单草稿。" };
  if (/买|下单|订/.test(text)) return { primary: "lead", support: ["product", "order"], risk: "低", action: "识别购买意向 · 补齐订单信息", reason: "检测到明确购买意向，但缺少商品或收货信息。", reply: "可以的。请告诉我商品、数量和收货城市，我会为您整理订单草稿。" };
  return { primary: "product", support: ["lead"], risk: "低", action: "产品问答 · 继续澄清需求", reason: "当前信息不足以进入交易流程。", reply: "您好，我可以协助产品咨询、报价、库存、订单与售后。请告诉我您想了解什么。" };
}

export default function Home() {
  const [message, setMessage] = useState(examples[0]);
  const [decision, setDecision] = useState<Decision>(() => routeMessage(examples[0]));
  const [pending, setPending] = useState(false);
  const [runs, setRuns] = useState(12);
  const support = useMemo(() => decision.support.map((item) => agents[item]).join(" + ") || "无", [decision]);
  function analyze(next = message) { if (!next.trim()) return; setMessage(next); setPending(true); window.setTimeout(() => { setDecision(routeMessage(next)); setRuns((n) => n + 1); setPending(false) }, 420) }

  return <main>
    <header className="nav shell"><a className="logo" href="#top"><span>F</span>Freshsales-Agent</a><nav><a href="#features">能力</a><a href="#demo">在线演示</a><a href="#architecture">架构</a></nav><a className="navButton" href="#demo">立即体验 <b>↗</b></a></header>
    <section className="hero shell" id="top">
      <div className="heroCopy"><div className="pill"><i /> AI-native sales operations</div><h1>让每一条客户消息，<br/><em>自动抵达正确的 Agent</em></h1><p>Freshsales-Agent 将产品咨询、报价、库存、订单、履约、售后和合规串成一个可解释、可审计的销售工作流。</p><div className="heroActions"><a className="primary" href="#demo">运行在线演示 <span>→</span></a><a className="secondary" href="https://github.com/liuluochen6-af/selfsale-agent" target="_blank" rel="noreferrer">查看 GitHub</a></div><small className="heroNote">无需登录 · 使用虚构数据 · 不连接真实客户系统</small></div>
      <div className="boardPreview" aria-label="Freshsales-Agent 工作台预览"><div className="previewTop"><div><span className="miniLogo">F</span><b>Sales Intelligence</b></div><span className="live"><i/>Live demo</span></div><div className="previewBody"><aside><small>AGENT NETWORK</small>{["报价 Agent","库存 Agent","产品顾问","合规 Agent"].map((name,i)=><div className={i===0?"agent active":"agent"} key={name}><span>{i+1}</span>{name}<i/></div>)}</aside><div className="previewMain"><div className="previewTitle"><div><small>ROUTING DECISION</small><b>报价 Agent</b></div><span>置信度 96%</span></div><div className="customerBubble">金枕现在多少钱，10箱有现货吗？</div><div className="routeSteps"><span>意图识别</span><i>→</i><span>库存校验</span><i>→</i><span>报价草案</span></div><div className="answer"><small>RECOMMENDED RESPONSE</small><p>金枕 A 果演示报价为 ¥428/箱，10 箱库存可用。</p></div></div></div></div>
    </section>
    <section className="proof"><div className="shell proofGrid"><div><strong>8</strong><span>专职业务 Agents</span></div><div><strong>3</strong><span>可复用 Agent Skills</span></div><div><strong>40</strong><span>自动化测试通过</span></div><div><strong>100%</strong><span>高风险动作受控</span></div></div></section>
    <section className="features shell" id="features"><div className="sectionIntro"><span>核心能力</span><h2>不只是回答问题，<br/>而是推进销售流程</h2><p>一个 Router 负责理解意图，八个业务 Agent 各司其职，高风险动作统一进入安全护栏。</p></div><div className="featureGrid"><article className="feature large"><div className="icon">⌁</div><small>01 · INTELLIGENT ROUTING</small><h3>一句话，自动拆解成执行计划</h3><p>从自由文本中提取意图、商品、数量和订单号，并给出主 Agent、协作 Agent、风险等级与下一步动作。</p><div className="miniFlow"><span>客户消息</span><b>→</b><span>Router</span><b>→</b><span className="accent">最合适 Agent</span></div></article><article className="feature"><div className="icon">◎</div><small>02 · HUMAN IN THE LOOP</small><h3>高风险动作不越权</h3><p>退款、赔付、隐私删除和停止联系自动阻断，转入人工复核。</p><div className="riskBar"><span>风险检测</span><b>高</b></div></article><article className="feature"><div className="icon">↗</div><small>03 · OBSERVABLE</small><h3>每个决定都有理由</h3><p>路由结果附带命中原因、置信度与结构化实体，方便排错与审计。</p><div className="codeLine">reason: “检测到报价 + 库存意图”</div></article></div></section>
    <section className="demoWrap" id="demo"><div className="shell"><div className="demoIntro"><div><span>在线体验</span><h2>把销售消息交给 Agent 网络</h2></div><p>选择示例或输入自己的消息。演示在浏览器本地运行，不发送或保存任何数据。</p></div><div className="workbench"><div className="workbenchHead"><div><span className="miniLogo">F</span><b>Freshsales-Agent Console</b></div><span className="live"><i/>System online</span></div><div className="workbenchBody"><aside className="agentRail"><small>AGENT NETWORK</small>{Object.entries(agents).map(([key,label])=><div key={key} className={decision.primary===key?"agent active":"agent"}><span>{label.slice(0,1)}</span>{label}<i/></div>)}</aside><div className="console"><div className="consoleTop"><div><small>ROUTING LAB</small><h3>消息决策工作台</h3></div><span>已运行 {runs} 次</span></div><div className="examples">{examples.map((item,i)=><button key={item} onClick={()=>analyze(item)}>0{i+1} {item.slice(0,12)}…</button>)}</div><label className="composer"><span>客户消息</span><textarea value={message} onChange={(e)=>setMessage(e.target.value)} onKeyDown={(e)=>{if((e.metaKey||e.ctrlKey)&&e.key==="Enter") analyze()}}/><button onClick={()=>analyze()} disabled={pending}>{pending?"分析中…":"运行 Agent →"}</button></label><div className={pending?"result loading":"result"}><div className="resultTop"><div><small>PRIMARY AGENT</small><h3>{agents[decision.primary]}</h3></div><span className={`risk risk-${decision.risk}`}>风险 · {decision.risk}</span></div><div className="decisionStats"><div><small>协作 Agent</small><b>{support}</b></div><div><small>系统动作</small><b>{decision.action}</b></div></div><div className="reason"><span>为什么这样路由</span><p>{decision.reason}</p></div><div className={decision.blocked?"reply blocked":"reply"}><small>{decision.blocked?"SAFE RESPONSE · 已阻断自动执行":"RECOMMENDED RESPONSE"}</small><p>{decision.reply}</p></div></div></div></div></div></div></section>
    <section className="architecture shell" id="architecture"><div className="sectionIntro center"><span>系统架构</span><h2>从消息入口到安全执行</h2><p>保留现有销售系统作为事实来源，智能层只负责理解、编排与建议。</p></div><div className="architectureFlow"><div><small>01</small><b>客户消息</b><span>Web / WeChat</span></div><i>→</i><div className="highlight"><small>02</small><b>Agent Router</b><span>意图 · 实体 · 风险</span></div><i>→</i><div><small>03</small><b>业务 Agents</b><span>8 个专职角色</span></div><i>→</i><div><small>04</small><b>SalesFlow</b><span>订单 · 库存 · 审计</span></div></div></section>
    <section className="cta"><div className="shell"><span>RECRUITER DEMO</span><h2>看见一个 Agent 系统<br/>如何真正进入业务流程</h2><a href="#demo">现在运行演示 →</a></div></section>
    <footer className="shell"><a className="logo" href="#top"><span>F</span>Freshsales-Agent</a><p>Multi-Agent Sales Intelligence OS · Recruiter-safe demo</p><a href="https://github.com/liuluochen6-af/selfsale-agent" target="_blank" rel="noreferrer">GitHub ↗</a></footer>
  </main>
}
