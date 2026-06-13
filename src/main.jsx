import React, { useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  AlertTriangle,
  BarChart3,
  Bot,
  CheckCircle2,
  Database,
  Download,
  ExternalLink,
  FileCode2,
  GitPullRequest,
  GitPullRequestArrow,
  Info,
  Scale,
  ShieldCheck,
  Sigma,
  Sparkles,
  UsersRound,
} from "lucide-react";
import data from "./data/impactData.json";
import "./styles.css";

const fmt = new Intl.NumberFormat("en-US");
const compact = new Intl.NumberFormat("en-US", { notation: "compact" });

const componentMeta = [
  ["deliveryScore", "Delivery", "Merged value, log-scaled by changed files", "#0f766e"],
  ["architectureScore", "Design", "Cross-area and stewardship work", "#7c3aed"],
  ["qualityScore", "Quality", "Fixes, perf, refactors, tests, infra", "#b45309"],
  ["collaborationScore", "Collab", "Reviews, threads, and discussion carried", "#be123c"],
];

const decisionCards = [
  {
    title: "What I built and why",
    icon: Sparkles,
    body: [
      "A PostHog engineering impact dashboard for a busy leader who needs the answer, evidence, and scoring logic in one place.",
      "I picked impact because PostHog's high-autonomy model makes raw activity misleading: the useful signal is value delivered while reducing complexity and helping other teams move.",
    ],
  },
  {
    title: "Key decisions and tradeoffs",
    icon: Scale,
    body: [
      "Merged PRs are the unit because they reached the mainline. File paths are log-scaled so giant PRs get diminishing returns.",
      "Home area is inferred from recent path history, which makes cross-boundary work measurable but can misclassify platform engineers and rotations.",
      "Component scores use empirical mid-rank percentiles instead of a P99 cap, so the leader can rank first without receiving four fake 100s.",
    ],
  },
  {
    title: "Intentionally left out",
    icon: ShieldCheck,
    body: [
      "Raw commit count, LoC, story points, and files changed as direct score drivers because they are easy to game.",
      "Meetings, sentiment, satisfaction, incidents, adoption, and full AI attribution because they are not complete inside GitHub for a 90-minute assignment.",
    ],
  },
  {
    title: "Breaks first under pressure",
    icon: AlertTriangle,
    body: [
      "Home-area inference breaks when people rotate teams, own platform surfaces, or repeatedly help outside their usual area.",
      "Review-thread load can reward contentious PRs unless comment quality, reviewer identity, and resolution quality are analyzed.",
      "GitHub pagination and API limits become the bottleneck if the window expands from 90 days to multiple years.",
    ],
  },
  {
    title: "What I would build next",
    icon: GitPullRequestArrow,
    body: [
      "Fetch review authors and comment bodies for the top candidate set to score mentorship and review quality.",
      "Join CODEOWNERS, incidents, release notes, and product analytics so the model can validate business and operational impact.",
      "Detect AI-assisted PRs and separate human design decisions from generated mechanical code.",
    ],
  },
];

const tabs = [
  { id: "overview", label: "Overview", icon: BarChart3 },
  { id: "formula", label: "Formula", icon: Sigma },
  { id: "decisions", label: "Decisions", icon: Scale },
];

function formatWindow(window) {
  const start = new Date(window.since);
  const end = new Date(window.until);
  return `${start.toLocaleDateString("en-US", { month: "short", day: "numeric" })} to ${end.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}`;
}

function ScoreBar({ value, color }) {
  return (
    <div className="scorebar" aria-label={`Score ${value}`}>
      <span style={{ width: `${Math.min(100, value)}%`, background: color }} />
    </div>
  );
}

function RankCard({ engineer, selected, onClick }) {
  return (
    <button className={`rank-card ${selected ? "selected" : ""}`} onClick={onClick}>
      <div className="rank-number">#{engineer.rank}</div>
      <div className="rank-main">
        <div className="rank-name">{engineer.login}</div>
        <div className="rank-sub">
          {engineer.homeArea.replace("_", " ")} - {engineer.mergedPrs} PRs
        </div>
        <div className="rank-components">
          <span>D {engineer.deliveryScore}</span>
          <span>A {engineer.architectureScore}</span>
          <span>Q {engineer.qualityScore}</span>
          <span>C {engineer.collaborationScore}</span>
        </div>
      </div>
      <div className="rank-score">{engineer.impactScore}</div>
    </button>
  );
}

function ComponentBars({ engineer }) {
  return (
    <div className="component-bars">
      {componentMeta.map(([key, label, description, color]) => (
        <div className="component-row" key={key}>
          <div>
            <strong>{label}</strong>
            <span>{description}</span>
          </div>
          <div className="component-score">
            <ScoreBar value={engineer[key]} color={color} />
            <b>{engineer[key]}</b>
          </div>
        </div>
      ))}
    </div>
  );
}

function FormulaPanel() {
  const formulas = [
    ["Scope", data.formula.scope, "F_p is unique changed file paths for PR p. Log scaling rewards focused changes and prevents giant PRs from dominating."],
    ["Delivery", data.formula.deliveryRaw, "W_intent gives product-facing, fixes, perf, and refactors different value weights. It is still delivery, not business revenue."],
    ["Design", data.formula.architectureRaw, "X_p means crossed the author's inferred home area. K_p is path category breadth. G_p is stewardship via tests, docs, or infra touched."],
    ["Quality", data.formula.qualityRaw, "Q gives credit to fixes, perf work, refactors, tests, infra, and stabilizing reverts. It does not claim deep semantic code quality."],
    ["Collaboration", data.formula.collaborationRaw, "R_p is review count, T_p is review thread count, and M_p is conversation comments on authored PRs."],
    ["Normalize", data.formula.normalize, "Each raw dimension becomes a mid-rank percentile among human engineers. A top engineer can be near 100, but no component is made perfect by crossing a cap."],
    ["Final", data.formula.impactScore, "The final score keeps delivery important, but leaves half the weight for design health, quality, and collaboration."],
  ];

  return (
    <section className="panel formula-panel">
      <div className="section-title">
        <Sigma size={18} />
        <div>
          <h2>Impact model</h2>
          <p>Transparent enough to audit, conservative enough to resist obvious gaming.</p>
        </div>
      </div>
      <div className="formula-callout">
        <Info size={16} />
        <p>
          I changed the old P99-capped normalization because it could show one engineer as 100 in every component.
          The current score is an empirical mid-rank percentile across {data.source.humanEngineers} human engineers.
        </p>
      </div>
      <div className="formula-list">
        {formulas.map(([name, formula, explanation]) => (
          <article className="formula-item" key={name}>
            <div className="formula-name">{name}</div>
            <code>{formula}</code>
            <p>{explanation}</p>
          </article>
        ))}
      </div>
    </section>
  );
}

function VariableDictionary() {
  const variables = [
    ["e", "Engineer being scored."],
    ["p", "A merged pull request authored during the 90-day window."],
    ["F_p", "Unique changed file paths for PR p, joined from local git history."],
    ["S_p", "Log-scaled PR scope: ln(1 + max(F_p, 1))."],
    ["W_intent", "Intent weight inferred from PR title prefix and labels, e.g. feat, fix, perf, refactor, docs."],
    ["X_p", "1 when a PR touches a core path outside the author's inferred home area; otherwise 0."],
    ["K_p", "Number of path categories touched: frontend, product backend, infra/CI, docs, tests, other."],
    ["G_p", "1 when the PR includes stewardship work: tests, docs, or infra/CI files."],
    ["Q_intent", "Quality stewardship weight for fixes, perf work, refactors, tests, infra, and reverts."],
    ["R_p", "Review count on the authored PR."],
    ["T_p", "Review discussion thread count on the authored PR."],
    ["M_p", "Conversation comment count on the authored PR."],
    ["N(x)", "Mid-rank percentile normalization among all human engineers."],
    ["EIS_e", "Weighted Engineering Impact Score for engineer e."],
  ];

  return (
    <section className="panel variable-panel">
      <div className="section-title">
        <Database size={18} />
        <div>
          <h2>Variable dictionary</h2>
          <p>What every symbol means and where the signal comes from.</p>
        </div>
      </div>
      <div className="variable-list">
        {variables.map(([symbol, meaning]) => (
          <div className="variable-row" key={symbol}>
            <code>{symbol}</code>
            <span>{meaning}</span>
          </div>
        ))}
      </div>
    </section>
  );
}

function EngineerDetail({ engineer }) {
  return (
    <section className="panel detail-panel">
      <div className="detail-head">
        <div>
          <p className="eyebrow">Selected engineer</p>
          <h2>{engineer.login}</h2>
          <span className="muted">Home area proxy: {engineer.homeArea.replace("_", " ")}</span>
        </div>
        <div className="big-score">
          <span>{engineer.impactScore}</span>
          <small>EIS</small>
        </div>
      </div>

      <ComponentBars engineer={engineer} />

      <div className="stat-grid">
        <div>
          <strong>{fmt.format(engineer.reviewCount)}</strong>
          <span>reviews received</span>
        </div>
        <div>
          <strong>{fmt.format(engineer.reviewThreadCount)}</strong>
          <span>review threads</span>
        </div>
        <div>
          <strong>{engineer.crossBoundaryRate}%</strong>
          <span>cross-boundary PRs</span>
        </div>
        <div>
          <strong>{engineer.medianCycleHours ? `${Math.round(engineer.medianCycleHours)}h` : "n/a"}</strong>
          <span>median cycle time</span>
        </div>
      </div>

      <div className="pr-list">
        <h3>Evidence PRs</h3>
        {engineer.topPrs.map((pr) => (
          <a href={pr.url} target="_blank" rel="noreferrer" className="pr-link" key={pr.number}>
            <span>#{pr.number}</span>
            <b>{pr.title}</b>
            <em>{pr.intent} - {pr.files} files - {pr.reviews} reviews</em>
            <ExternalLink size={14} />
          </a>
        ))}
      </div>
    </section>
  );
}

function MethodNotes() {
  return (
    <section className="decision-grid">
      {decisionCards.map(({ title, icon: Icon, body }) => (
        <article className="decision-card" key={title}>
          <Icon size={18} />
          <h3>{title}</h3>
          {body.map((line) => (
            <p key={line}>{line}</p>
          ))}
        </article>
      ))}
    </section>
  );
}

function RequirementPanel() {
  const checks = [
    ["90-day data", `${formatWindow(data.source.window)} from PostHog/posthog.`],
    ["Real GitHub signals", `${fmt.format(data.source.counts.merged_prs)} merged PRs, ${fmt.format(data.source.counts.master_commit_file_rows)} commit-file rows, and ${fmt.format(data.source.collaborationTotals)} PR collaboration totals.`],
    ["Top 5 answer", "The leaderboard shows the five most impactful engineers and lets the leader inspect each engineer's component scores and evidence PRs."],
    ["Auditable model", "Formula and Decisions tabs explain variables, weights, tradeoffs, omissions, and failure modes."],
    ["Hosted artifact", "Static JSON and CSV are bundled at build time, so the dashboard loads without a GitHub token in the browser."],
  ];

  return (
    <section className="panel requirement-panel">
      <div className="section-title">
        <CheckCircle2 size={18} />
        <div>
          <h2>Requirement check</h2>
          <p>What this page covers before submission.</p>
        </div>
      </div>
      <div className="requirement-list">
        {checks.map(([label, detail]) => (
          <div className="requirement-row" key={label}>
            <CheckCircle2 size={15} />
            <div>
              <strong>{label}</strong>
              <span>{detail}</span>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function DataStrip() {
  const source = data.source;
  return (
    <section className="data-strip">
      <div>
        <GitPullRequest size={17} />
        <strong>{fmt.format(source.counts.merged_prs)}</strong>
        <span>merged PRs</span>
      </div>
      <div>
        <FileCode2 size={17} />
        <strong>{fmt.format(source.counts.master_commit_file_rows)}</strong>
        <span>commit-file rows</span>
      </div>
      <div>
        <UsersRound size={17} />
        <strong>{fmt.format(source.humanEngineers)}</strong>
        <span>human engineers</span>
      </div>
      <div>
        <Bot size={17} />
        <strong>{fmt.format(source.excludedBotPrs)}</strong>
        <span>bot PRs excluded</span>
      </div>
      <div>
        <CheckCircle2 size={17} />
        <strong>{source.gitJoinCoveragePct}%</strong>
        <span>PR/git join coverage</span>
      </div>
    </section>
  );
}

function Leaderboard({ selected, onSelect }) {
  return (
    <section className="panel leaderboard-panel">
      <div className="section-title">
        <BarChart3 size={18} />
        <div>
          <h2>Top 5 impact</h2>
          <p>D/A/Q/C are component percentiles.</p>
        </div>
      </div>
      <div className="rank-list">
        {data.topFive.map((engineer) => (
          <RankCard
            engineer={engineer}
            key={engineer.login}
            selected={selected.login === engineer.login}
            onClick={() => onSelect(engineer)}
          />
        ))}
      </div>
    </section>
  );
}

function App() {
  const [selectedLogin, setSelectedLogin] = useState(data.topFive[0].login);
  const [activeTab, setActiveTab] = useState("overview");
  const selected = useMemo(
    () => data.rankedEngineers.find((engineer) => engineer.login === selectedLogin) || data.topFive[0],
    [selectedLogin]
  );

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">PostHog Engineering Impact Dashboard</p>
          <h1>Who created the most engineering impact in the last 90 days?</h1>
        </div>
        <div className="topbar-actions">
          <a className="download-link" href="/impact_engineers.csv" download>
            <Download size={16} />
            Download CSV
          </a>
          <div className="window-pill">
            <span>{formatWindow(data.source.window)}</span>
            <b>{compact.format(data.source.counts.merged_prs)} PRs analyzed</b>
          </div>
        </div>
      </header>

      <DataStrip />

      <nav className="tabbar" aria-label="Dashboard sections">
        {tabs.map(({ id, label, icon: Icon }) => (
          <button
            className={activeTab === id ? "active" : ""}
            key={id}
            onClick={() => setActiveTab(id)}
            type="button"
          >
            <Icon size={16} />
            {label}
          </button>
        ))}
      </nav>

      {activeTab === "overview" && (
        <div className="overview-grid">
          <Leaderboard selected={selected} onSelect={(engineer) => setSelectedLogin(engineer.login)} />
          <EngineerDetail engineer={selected} />
          <RequirementPanel />
        </div>
      )}

      {activeTab === "formula" && (
        <div className="formula-grid">
          <FormulaPanel />
          <VariableDictionary />
        </div>
      )}

      {activeTab === "decisions" && <MethodNotes />}

      <footer>
        <span>Source: GitHub REST Search, GraphQL PR collaboration totals, and local master-branch git path history.</span>
        <span>Generated {new Date(data.generatedAt).toLocaleString()}</span>
      </footer>
    </main>
  );
}

createRoot(document.getElementById("root")).render(<App />);
