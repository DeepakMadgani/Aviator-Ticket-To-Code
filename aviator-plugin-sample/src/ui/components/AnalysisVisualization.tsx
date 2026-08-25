/**
 * AnalysisVisualization Component
 *
 * Displays comprehensive analysis of ticket-to-code execution:
 * - Workspace symbol index (existing properties/methods by file)
 * - Planner decisions (what will be created/modified)
 * - Conflicts detected (duplicates, type mismatches)
 * - Refactoring operations (renames across files)
 * - Edit loop progress (real-time updates as generation happens)
 *
 * Author: Deepak Madgani
 * Date: August 2026
 */

import React, { useState, useCallback } from "react";
import "./AnalysisVisualization.css";

// Type definitions matching Python backend

interface SymbolInfo {
  name: string;
  kind: "property" | "method" | "class" | "interface" | "enum" | "function";
  type_hint?: string;
  access_level?: string;
  is_optional?: boolean;
  source_line?: number;
}

interface FileSymbols {
  file_path: string;
  language: string;
  symbols: SymbolInfo[];
  class_names: string[];
  extraction_method: string;
  error?: string;
}

interface PropertyConflict {
  property_name: string;
  ts_file: string;
  html_file: string;
  conflict_type: string;
  resolution: string;
  ts_has_property: boolean;
  html_references_property: boolean;
  reason: string;
}

interface EditLoopProgress {
  iteration: number;
  max_iterations: number;
  current_file: string;
  status: "pending" | "in-progress" | "completed" | "error";
  generated_properties: string[];
  verified_errors: string[];
  timestamp: string;
}

interface AnalysisVisualizationProps {
  workspaceSymbols?: Record<string, FileSymbols>;
  propertyConflicts?: PropertyConflict[];
  plannerDecisions?: Record<string, object>;
  editLoopProgress?: EditLoopProgress[];
  isLoading?: boolean;
}

/**
 * Main Analysis Visualization Component
 */
const AnalysisVisualization: React.FC<AnalysisVisualizationProps> = ({
  workspaceSymbols = {},
  propertyConflicts = [],
  plannerDecisions = {},
  editLoopProgress = [],
  isLoading = false,
}) => {
  const [activeTab, setActiveTab] = useState<
    "symbols" | "conflicts" | "planner" | "editloop"
  >("symbols");
  const [expandedFile, setExpandedFile] = useState<string | null>(null);

  return (
    <div className="analysis-visualization">
      <div className="av-header">
        <h2>🔍 Ticket Analysis & Generation Progress</h2>
        {isLoading && <span className="av-loading">Analyzing...</span>}
      </div>

      <div className="av-tabs">
        <button
          className={`av-tab ${activeTab === "symbols" ? "active" : ""}`}
          onClick={() => setActiveTab("symbols")}
        >
          📦 Workspace Symbols ({Object.keys(workspaceSymbols).length})
        </button>
        <button
          className={`av-tab ${activeTab === "conflicts" ? "active" : ""}`}
          onClick={() => setActiveTab("conflicts")}
        >
          ⚠️ Conflicts ({propertyConflicts.length})
        </button>
        <button
          className={`av-tab ${activeTab === "planner" ? "active" : ""}`}
          onClick={() => setActiveTab("planner")}
        >
          📋 Planner ({Object.keys(plannerDecisions).length})
        </button>
        <button
          className={`av-tab ${activeTab === "editloop" ? "active" : ""}`}
          onClick={() => setActiveTab("editloop")}
        >
          ♻️ Edit Loop ({editLoopProgress.length})
        </button>
      </div>

      <div className="av-content">
        {activeTab === "symbols" && (
          <SymbolsPanel
            workspaceSymbols={workspaceSymbols}
            expandedFile={expandedFile}
            setExpandedFile={setExpandedFile}
          />
        )}

        {activeTab === "conflicts" && (
          <ConflictsPanel propertyConflicts={propertyConflicts} />
        )}

        {activeTab === "planner" && (
          <PlannerPanel plannerDecisions={plannerDecisions} />
        )}

        {activeTab === "editloop" && (
          <EditLoopPanel editLoopProgress={editLoopProgress} />
        )}
      </div>
    </div>
  );
};

/**
 * Symbols Panel: Show all extracted symbols by file
 */
const SymbolsPanel: React.FC<{
  workspaceSymbols: Record<string, FileSymbols>;
  expandedFile: string | null;
  setExpandedFile: (file: string | null) => void;
}> = ({ workspaceSymbols, expandedFile, setExpandedFile }) => {
  const summary = Object.entries(workspaceSymbols).reduce(
    (acc, [_, fs]) => {
      acc.files += 1;
      acc.symbols += fs.symbols.length;
      acc.properties += fs.symbols.filter((s) => s.kind === "property").length;
      acc.methods += fs.symbols.filter((s) => s.kind === "method").length;
      return acc;
    },
    { files: 0, symbols: 0, properties: 0, methods: 0 }
  );

  return (
    <div className="av-panel symbols-panel">
      <div className="av-summary">
        <div className="summary-stat">
          <strong>📁 Files</strong> <span>{summary.files}</span>
        </div>
        <div className="summary-stat">
          <strong>📍 Total Symbols</strong> <span>{summary.symbols}</span>
        </div>
        <div className="summary-stat">
          <strong>🏷️ Properties</strong> <span>{summary.properties}</span>
        </div>
        <div className="summary-stat">
          <strong>⚙️ Methods</strong> <span>{summary.methods}</span>
        </div>
      </div>

      <div className="av-file-list">
        {Object.entries(workspaceSymbols).map(([filePath, fileSymbols]) => (
          <div
            key={filePath}
            className={`av-file-item ${
              expandedFile === filePath ? "expanded" : ""
            }`}
          >
            <div
              className="av-file-header"
              onClick={() =>
                setExpandedFile(expandedFile === filePath ? null : filePath)
              }
            >
              <span className="av-file-icon">
                {getLanguageIcon(fileSymbols.language)}
              </span>
              <span className="av-file-path">{filePath}</span>
              <span className="av-symbol-count">
                {fileSymbols.symbols.length} symbols
              </span>
              <span className={`av-extraction-method ${fileSymbols.extraction_method}`}>
                {fileSymbols.extraction_method}
              </span>
            </div>

            {expandedFile === filePath && (
              <div className="av-file-details">
                {fileSymbols.symbols.length === 0 ? (
                  <div className="av-empty">No symbols found</div>
                ) : (
                  <>
                    {/* Group by kind */}
                    {["property", "method", "class", "interface"].map((kind) => {
                      const kindSymbols = fileSymbols.symbols.filter(
                        (s) => s.kind === kind
                      );
                      if (kindSymbols.length === 0) return null;

                      return (
                        <div key={kind} className="av-symbol-group">
                          <div className="av-group-title">
                            {getKindEmoji(kind)} {kind}s ({kindSymbols.length})
                          </div>
                          <div className="av-symbol-list">
                            {kindSymbols.map((sym) => (
                              <div key={sym.name} className="av-symbol">
                                <span className="av-symbol-name">{sym.name}</span>
                                {sym.type_hint && (
                                  <span className="av-symbol-type">
                                    : {sym.type_hint}
                                  </span>
                                )}
                                {sym.access_level && (
                                  <span
                                    className={`av-symbol-access ${sym.access_level}`}
                                  >
                                    {sym.access_level}
                                  </span>
                                )}
                              </div>
                            ))}
                          </div>
                        </div>
                      );
                    })}
                  </>
                )}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
};

/**
 * Conflicts Panel: Show detected property/method conflicts
 */
const ConflictsPanel: React.FC<{ propertyConflicts: PropertyConflict[] }> = ({
  propertyConflicts,
}) => {
  if (propertyConflicts.length === 0) {
    return (
      <div className="av-panel">
        <div className="av-empty">✅ No conflicts detected! All symbols are consistent.</div>
      </div>
    );
  }

  const groupedByType = propertyConflicts.reduce(
    (acc, conflict) => {
      const type = conflict.conflict_type;
      if (!acc[type]) acc[type] = [];
      acc[type].push(conflict);
      return acc;
    },
    {} as Record<string, PropertyConflict[]>
  );

  return (
    <div className="av-panel conflicts-panel">
      <div className="av-conflict-summary">
        Found <strong>{propertyConflicts.length}</strong> potential conflicts
      </div>

      {Object.entries(groupedByType).map(([type, conflicts]) => (
        <div key={type} className="av-conflict-group">
          <div className="av-conflict-group-title">
            {getConflictIcon(type)} {type} ({conflicts.length})
          </div>
          <div className="av-conflict-list">
            {conflicts.map((conflict, idx) => (
              <div key={idx} className={`av-conflict ${conflict.conflict_type}`}>
                <div className="av-conflict-header">
                  <span className="av-conflict-property">
                    {conflict.property_name}
                  </span>
                  <span className={`av-conflict-resolution ${conflict.resolution}`}>
                    {conflict.resolution}
                  </span>
                </div>
                <div className="av-conflict-details">
                  <div className="av-conflict-files">
                    <span className="av-file-label">TS:</span>{" "}
                    <code>{conflict.ts_file}</code>
                    <span className="av-file-label">HTML:</span>{" "}
                    <code>{conflict.html_file}</code>
                  </div>
                  <div className="av-conflict-reason">{conflict.reason}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
};

/**
 * Planner Panel: Show planner decisions
 */
const PlannerPanel: React.FC<{ plannerDecisions: Record<string, object> }> = ({
  plannerDecisions,
}) => {
  const decisions = Object.entries(plannerDecisions);

  const groupedByDecision = decisions.reduce(
    (acc, [filePath, decision]: [string, any]) => {
      const decisionType = decision.decision || "unknown";
      if (!acc[decisionType]) acc[decisionType] = [];
      acc[decisionType].push({ filePath, ...decision });
      return acc;
    },
    {} as Record<string, object[]>
  );

  return (
    <div className="av-panel planner-panel">
      <div className="av-planner-summary">
        Planner evaluated <strong>{decisions.length}</strong> candidates
      </div>

      {Object.entries(groupedByDecision).map(([decision, items]) => (
        <div key={decision} className="av-planner-group">
          <div className="av-planner-group-title">
            {getPlannerDecisionIcon(decision as string)} {decision} (
            {(items as any[]).length})
          </div>
          <div className="av-planner-list">
            {(items as any[]).map((item, idx) => (
              <div key={idx} className={`av-planner-item ${decision}`}>
                <div className="av-planner-file">
                  <code>{item.file_path}</code>
                </div>
                <div className="av-planner-info">
                  <span className="av-planner-role">{item.role}</span>
                  <span className="av-planner-confidence">
                    {(item.confidence * 100).toFixed(0)}%
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
};

/**
 * Edit Loop Panel: Real-time progress of generation
 */
const EditLoopPanel: React.FC<{ editLoopProgress: EditLoopProgress[] }> = ({
  editLoopProgress,
}) => {
  if (editLoopProgress.length === 0) {
    return (
      <div className="av-panel">
        <div className="av-empty">⏳ Waiting for generation to start...</div>
      </div>
    );
  }

  const latest = editLoopProgress[editLoopProgress.length - 1];
  const completed =
    editLoopProgress.filter((p) => p.status === "completed").length;
  const errors = editLoopProgress.filter((p) => p.status === "error").length;

  return (
    <div className="av-panel editloop-panel">
      <div className="av-editloop-progress">
        <div className="av-progress-bar">
          <div
            className="av-progress-fill"
            style={{
              width: `${(completed / latest.max_iterations) * 100}%`,
            }}
          />
          <span className="av-progress-text">
            {completed}/{latest.max_iterations}
          </span>
        </div>
        {errors > 0 && (
          <div className="av-progress-errors">❌ {errors} errors encountered</div>
        )}
      </div>

      <div className="av-editloop-iterations">
        {editLoopProgress.map((progress, idx) => (
          <div key={idx} className={`av-iteration ${progress.status}`}>
            <div className="av-iteration-header">
              <span className="av-iteration-number">#{progress.iteration}</span>
              <span className="av-iteration-file">{progress.current_file}</span>
              <span className={`av-iteration-status ${progress.status}`}>
                {getStatusEmoji(progress.status)} {progress.status}
              </span>
              <span className="av-iteration-time">{progress.timestamp}</span>
            </div>
            {progress.generated_properties.length > 0 && (
              <div className="av-iteration-details">
                <div className="av-detail-label">Generated:</div>
                {progress.generated_properties.map((prop) => (
                  <span key={prop} className="av-detail-tag">
                    {prop}
                  </span>
                ))}
              </div>
            )}
            {progress.verified_errors.length > 0 && (
              <div className="av-iteration-errors">
                <div className="av-error-label">Errors:</div>
                {progress.verified_errors.map((err) => (
                  <div key={err} className="av-error-message">
                    {err}
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
};

// ============================================================================
// UTILITY FUNCTIONS
// ============================================================================

function getLanguageIcon(language: string): string {
  const icons: Record<string, string> = {
    typescript: "🟦",
    javascript: "🟨",
    java: "☕",
    python: "🐍",
    html: "🌐",
    csharp: "🟪",
    kotlin: "🎯",
    unknown: "❓",
  };
  return icons[language] || "📄";
}

function getKindEmoji(kind: string): string {
  const emojis: Record<string, string> = {
    property: "🏷️",
    method: "⚙️",
    class: "📦",
    interface: "🔷",
    enum: "🔢",
    function: "ƒ",
  };
  return emojis[kind] || "❓";
}

function getConflictIcon(type: string): string {
  const icons: Record<string, string> = {
    duplicate_in_both: "🔴",
    duplicate_in_ts: "🟠",
    duplicate_in_html: "🟡",
    missing_in_ts: "❌",
    missing_in_html: "⚠️",
    name_mismatch: "🔀",
    type_mismatch: "🔢",
  };
  return icons[type] || "❓";
}

function getPlannerDecisionIcon(decision: string): string {
  const icons: Record<string, string> = {
    REQUIRED: "✅",
    OPTIONAL: "ℹ️",
    IGNORE: "⊘",
  };
  return icons[decision] || "❓";
}

function getStatusEmoji(status: string): string {
  const emojis: Record<string, string> = {
    pending: "⏳",
    "in-progress": "⚙️",
    completed: "✅",
    error: "❌",
  };
  return emojis[status] || "❓";
}

export default AnalysisVisualization;
export type {
  SymbolInfo,
  FileSymbols,
  PropertyConflict,
  EditLoopProgress,
  AnalysisVisualizationProps,
};
