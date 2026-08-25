// Transparent Workflow Component - Shows all steps with human approval checkpoints
import React, { useState, useEffect, useRef, useCallback } from 'react';
import './TransparentWorkflow.css';

// ── Smart agent output renderer ───────────────────────────────────────────
function AgentOutputRenderer({ output, node }) {
  if (!output) return null;

  // The backend now sends clean, human-readable summaries.
  // Just unescape \n so text wraps correctly and render as formatted text.
  const cleaned = typeof output === 'string'
    ? output.replace(/\\n/g, '\n').replace(/\\t/g, '\t')
    : String(output);

  return (
    <pre style={{ margin: 0, fontSize: 12, color: '#c8e6fa', whiteSpace: 'pre-wrap',
      overflowWrap: 'anywhere', wordBreak: 'break-word', maxWidth: '100%', overflow: 'hidden',
      fontFamily: "'Courier New', monospace", lineHeight: 1.5 }}>{cleaned}</pre>
  );
}

const API = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8002';
const WS_BASE_URL = API.replace(/^http/, 'ws');

const PHASE_LABELS = {
  idle: 'Idle',
  classification: 'Classification',
  requirements: 'Requirements Analysis',
  planning: 'Implementation Planning',
  localization: 'File Localization',
  impact_analysis: 'Impact Analysis',
  human_review_files: 'Human Review',
  context_loading: 'Loading Context',
  patch_generation: 'Generating Patches',
  validation: 'Validation',
  human_final_review: 'Final Review',
  committing: 'Committing Changes',
  completed: 'Completed',
  failed: 'Failed',
  processing: 'Processing',
};

const NODE_ICONS = {
  investigate:     '🔍',
  runtime_diagnosis: '🩺',
  unified_analysis:'🧠',
  plan:            '📐',
  rag_tests:       '🧪',
  rag_code:        '📚',
  generate_tests:  '🧪',
  generate_code:   '💻',
  edit_loop:       '✏️',
  patch_gate:      '🛡️',
  angular_module_registration: '📦',
  build:           '🔨',
  outcome_verification: '🎯',
  outcome_check:   '🎯',
  test:            '✅',
  fix_build:       '🔧',
  fix_test:        '🔧',
};

const TransparentWorkflow = ({ projectId, ticketId, ticketDescription, repoPath, attachments = [], onWorkflowStarted, existingWorkflowId }) => {
  const [workflowId, setWorkflowId]         = useState(existingWorkflowId || null);
  const [workflowState, setWorkflowState]   = useState(null);
  const [steps, setSteps]                   = useState([]);
  const [selectedFiles, setSelectedFiles]   = useState([]);
  const [excludedChunks, setExcludedChunks] = useState(new Set()); // chunk paths excluded from RAG
  const [showFileExplorer, setShowFileExplorer] = useState(false);
  const [showDecisionDebug, setShowDecisionDebug] = useState(false);
  const [expandedSteps, setExpandedSteps]   = useState(new Set());
  const [activeTab, setActiveTab]           = useState('timeline'); // 'timeline' | 'rag'

  const startedRef   = useRef(false);
  const pollTimer    = useRef(null);
  const wsRef        = useRef(null);

  // -- helpers --------------------------------------------------------------

  const fetchState = useCallback(async (wfId) => {
    try {
      const res  = await fetch(`${API}/api/workflow/transparent/${wfId}`);
      const data = await res.json();
      setWorkflowState(data);

      // seed steps from persisted list (for late-joining / reconnect)
      if (data.steps && data.steps.length > 0) {
        setSteps(data.steps);
      }

      // seed selected files from candidates
      if (data.candidate_files && data.candidate_files.length > 0) {
        setSelectedFiles(prev =>
          prev.length > 0 ? prev :
          data.candidate_files.filter(f => f.selected).map(f => f.path)
        );
      }

      return data;
    } catch {
      return null;
    }
  }, []);

  const startPolling = useCallback((wfId) => {
    const tick = async () => {
      const state = await fetchState(wfId);
      if (state && state.status === 'running') {
        pollTimer.current = setTimeout(tick, 1500);
      }
    };
    tick();
  }, [fetchState]);

  const connectWS = useCallback((wfId) => {
    const socket = new WebSocket(`${WS_BASE_URL}/ws/workflow/${wfId}`);
    wsRef.current = socket;

    socket.onmessage = (event) => {
      try {
        const step = JSON.parse(event.data);
        setSteps(prev => {
          // de-duplicate by timestamp
          const seen = new Set(prev.map(s => s.timestamp));
          return seen.has(step.timestamp) ? prev : [...prev, step];
        });
        fetchState(wfId);
      } catch { /* ignore parse errors */ }
    };

    socket.onerror = () => {};  // handled by onclose
    socket.onclose = () => {
      // Reconnect after 3 s if workflow still running
      setTimeout(() => {
        if (wsRef.current === socket) connectWS(wfId);
      }, 3000);
    };
  }, [fetchState]);

  // -- start workflow once on mount -----------------------------------------

  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;

    (async () => {
      try {
        let wfId = existingWorkflowId;

        if (!wfId) {
          // New ticket — start the workflow via API
          const res = await fetch(`${API}/api/workflow/transparent/start`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              project_id: projectId,
              ticket_id: ticketId,
              ticket_description: ticketDescription,
              repo_path: repoPath,
              attachments: attachments || [],
            }),
          });
          const data = await res.json();
          wfId = data.workflow_id;
          setWorkflowId(wfId);
          if (onWorkflowStarted) onWorkflowStarted(wfId);
        } else {
          // Reconnecting to an existing run — just fetch current state, no new start
          setWorkflowId(wfId);
        }

        await fetchState(wfId);
        startPolling(wfId);
        connectWS(wfId);
      } catch (err) {
        console.error('Failed to start workflow:', err);
      }
    })();

    return () => {
      clearTimeout(pollTimer.current);
      if (wsRef.current) wsRef.current.close();
    };
  }, []); // empty deps — run once

  // -- approve helpers -------------------------------------------------------

  const approveOperation = async () => {
    await fetch(`${API}/api/workflow/transparent/approve-operation`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ workflow_id: workflowId }),
    });
    fetchState(workflowId);
  };

  const approveFiles = async () => {
    await fetch(`${API}/api/workflow/transparent/approve-files`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ workflow_id: workflowId, selected_files: selectedFiles }),
    });
    fetchState(workflowId);
  };

  const toggleFile = (path) =>
    setSelectedFiles(prev =>
      prev.includes(path) ? prev.filter(p => p !== path) : [...prev, path]
    );

  const toggleChunk = (path) =>
    setExcludedChunks(prev => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path); else next.add(path);
      return next;
    });

  const toggleStep = (i) =>
    setExpandedSteps(prev => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i); else next.add(i);
      return next;
    });

  // Collect ALL rag_chunks across all steps, deduplicated by path, sorted by score
  const allRagChunks = (() => {
    const seen = new Map(); // path -> chunk (keep highest score)
    for (const step of steps) {
      for (const ch of (step.data?.rag_chunks || [])) {
        if (!seen.has(ch.path) || ch.score > seen.get(ch.path).score)
          seen.set(ch.path, ch);
      }
    }
    return [...seen.values()].sort((a, b) => b.score - a.score);
  })();

  // -- render ----------------------------------------------------------------

  if (!workflowId || !workflowState) {
    return (
      <div className="workflow-loading">
        <div className="spinner"></div>
        <p>Initializing workflow…</p>
      </div>
    );
  }

  const isRunning   = workflowState.status === 'running';
  const isCompleted = workflowState.status === 'completed';
  const isFailed    = workflowState.status === 'failed';

  return (
    <div className="transparent-workflow">

      {/* -- Header -- */}
      <div className="workflow-header">
        <h2>Transparent Workflow</h2>
        <div className="workflow-meta">
          <span className="ticket-id">Ticket: {workflowState.ticket_id}</span>
          <span className={`phase-badge phase-${workflowState.current_phase}`}>
            {isRunning && <span className="spinner-sm" />}
            {PHASE_LABELS[workflowState.current_phase] ?? workflowState.current_phase}
          </span>
        </div>
      </div>

      {/* -- Tabs: Timeline | RAG Context -- */}
      <div className="wf-tabs">
        <button
          className={`wf-tab ${activeTab === 'timeline' ? 'active' : ''}`}
          onClick={() => setActiveTab('timeline')}
        >
          📋 Agent Steps ({steps.length})
        </button>
        <button
          className={`wf-tab ${activeTab === 'rag' ? 'active' : ''}`}
          onClick={() => setActiveTab('rag')}
        >
          🔍 RAG Context ({allRagChunks.length})
          {allRagChunks.length > 0 && (
            <span className="tab-badge">{allRagChunks.filter(c => !excludedChunks.has(c.path)).length} active</span>
          )}
        </button>
      </div>

      {/* ═══════════════════ TAB: TIMELINE ═══════════════════ */}
      {activeTab === 'timeline' && (
        <div className="workflow-timeline">
          <div className="timeline-steps">
            {steps.length === 0 && isRunning && (
              <div className="timeline-waiting">
                <span className="spinner-sm" /> Waiting for first agent step…
              </div>
            )}
            {steps.map((step, i) => {
              const nodeIcon = NODE_ICONS[step.data?.node] || '⚙️';
              const isExpanded = expandedSteps.has(i);
              const hasOutput = !!(step.data?.agent_output);
              const hasRag = !!(step.data?.rag_chunks?.length);
              const eventType = step.data?.event_type;

              // ── Compact file event cards (live-check progress) ──
              if (eventType) {
                const STATUS_STYLES = {
                  file_written:        { icon: '📝', bg: '#0d2137', border: '#1e3a5c', color: '#7ec8e3' },
                  live_check_start:    { icon: '⚠️', bg: '#2d1b00', border: '#c68a00', color: '#fbbf24' },
                  live_check_resolved: { icon: '✅', bg: '#0b2618', border: '#22c55e', color: '#86efac' },
                  live_check_passed:   { icon: '✅', bg: '#0b2618', border: '#22c55e', color: '#86efac' },
                  live_check_retry:    { icon: '🔄', bg: '#1e1b2e', border: '#8b5cf6', color: '#c4b5fd' },
                  live_check_deferred: { icon: '⏭️', bg: '#2d1b00', border: '#f59e0b', color: '#fde68a' },
                };
                const style = STATUS_STYLES[eventType] || STATUS_STYLES.file_written;
                const fileName = step.data?.file_name || step.data?.file_path?.split('/').pop() || '?';
                const errors = step.data?.errors || [];
                const hasErrors = errors.length > 0;

                return (
                  <div key={i} className="file-event-card" style={{
                    display: 'flex', alignItems: 'center', gap: 10,
                    padding: '6px 12px', marginBottom: 4, borderRadius: 6,
                    background: style.bg, border: `1px solid ${style.border}`,
                    fontSize: 13, color: style.color, cursor: hasErrors ? 'pointer' : 'default',
                  }} onClick={() => hasErrors && toggleStep(i)}>
                    <span style={{ fontSize: 16 }}>{style.icon}</span>
                    <code style={{ fontFamily: "'Courier New', monospace", fontSize: 12,
                      background: '#0a1929', padding: '1px 6px', borderRadius: 4 }}>
                      {fileName}
                    </code>
                    <span style={{ flex: 1, opacity: 0.85 }}>{step.message}</span>
                    {step.data?.change_ratio && (
                      <span style={{ fontSize: 11, opacity: 0.6 }}>Δ {step.data.change_ratio}</span>
                    )}
                    {step.data?.attempt && (
                      <span style={{ fontSize: 11, background: '#1a1a2e', padding: '1px 6px',
                        borderRadius: 4 }}>attempt {step.data.attempt}/3</span>
                    )}
                    {step.data?.remaining_errors != null && (
                      <span style={{ fontSize: 11, color: '#f87171' }}>
                        {step.data.remaining_errors} error(s)
                      </span>
                    )}
                    {step.data?.files_fixed?.length > 0 && (
                      <span style={{ fontSize: 11, color: '#86efac' }}>
                        {step.data.files_fixed.length} file(s) fixed
                      </span>
                    )}
                    {hasErrors && (
                      <span style={{ fontSize: 10, opacity: 0.5 }}>
                        {isExpanded ? '▲' : '▼'}
                      </span>
                    )}
                    <span style={{ fontSize: 10, opacity: 0.4 }}>
                      {step.timestamp ? new Date(step.timestamp).toLocaleTimeString() : ''}
                    </span>
                    {/* Expandable error details */}
                    {hasErrors && isExpanded && (
                      <div style={{ position: 'absolute', left: 0, right: 0, top: '100%',
                        background: '#0a1929', border: `1px solid ${style.border}`,
                        borderRadius: '0 0 6px 6px', padding: '6px 12px', zIndex: 10 }}
                        onClick={(e) => e.stopPropagation()}>
                        {errors.map((err, j) => (
                          <div key={j} style={{ fontSize: 11, color: '#f87171',
                            fontFamily: 'monospace', padding: '2px 0',
                            whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                            {err}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                );
              }

              // ── Standard timeline step (non-file-event) ──
              return (
                <div key={i} className={`timeline-step status-${step.status}`}>
                  <div className="step-marker">{nodeIcon}</div>
                  <div className="step-content">
                    <div className="step-header" onClick={() => hasOutput && toggleStep(i)}
                         style={{ cursor: hasOutput ? 'pointer' : 'default' }}>
                      <span className="step-phase">
                        {PHASE_LABELS[step.phase] ?? step.phase}
                        {step.data?.node && <span className="step-node"> [{step.data.node}]</span>}
                      </span>
                      <div className="step-header-right">
                        {hasRag && <span className="rag-badge">🔍 {step.data.rag_chunks.length} RAG</span>}
                        {hasOutput && (
                          <span className="expand-btn">{isExpanded ? '▲ Hide' : '▼ Details'}</span>
                        )}
                        <span className="step-time">
                          {step.timestamp ? new Date(step.timestamp).toLocaleTimeString() : ''}
                        </span>
                      </div>
                    </div>

                    <p className="step-message">{step.message}</p>

                    {/* Agent Output — collapsible */}
                    {hasOutput && isExpanded && (
                      <div className="agent-output-panel" style={{ overflow: 'hidden', maxWidth: '100%' }}>
                        <div className="agent-output-label">Agent Output:</div>
                        <AgentOutputRenderer
                          output={step.data.agent_output}
                          node={step.data?.node}
                        />
                      </div>
                    )}

                    {/* RAG chunks inline summary (when step has chunks) */}
                    {hasRag && isExpanded && (
                      <div className="rag-inline">
                        <div className="rag-inline-label">RAG chunks used:</div>
                        {step.data.rag_chunks.map((ch, j) => {
                          const excluded = excludedChunks.has(ch.path);
                          const pct = Math.round(ch.score * 100);
                          return (
                            <div key={j} className={`rag-chunk-row ${excluded ? 'excluded' : ''}`}>
                              <input
                                type="checkbox"
                                checked={!excluded}
                                onChange={() => toggleChunk(ch.path)}
                                title={excluded ? 'Include in RAG' : 'Exclude from RAG'}
                              />
                              <div className="rag-chunk-score-bar">
                                <div className="rag-score-fill" style={{ width: `${pct}%`,
                                  background: pct >= 70 ? '#22c55e' : pct >= 40 ? '#f59e0b' : '#ef4444'
                                }} />
                              </div>
                              <span className="rag-chunk-pct">{pct}%</span>
                              <span className="rag-chunk-label">{ch.rag_label}</span>
                              <code className="rag-chunk-path">{ch.path.split('/').slice(-2).join('/')}</code>
                              {ch.name && <span className="rag-chunk-name">({ch.name})</span>}
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* ═══════════════════ TAB: RAG CONTEXT ═══════════════════ */}
      {activeTab === 'rag' && (
        <div className="rag-context-panel">
          {allRagChunks.length === 0 ? (
            <div className="rag-empty">
              {isRunning
                ? '⏳ RAG retrieval not yet started…'
                : '⚠️ No RAG context was retrieved for this ticket.'}
            </div>
          ) : (
            <>
              <div className="rag-panel-info">
                Showing {allRagChunks.length} unique chunks retrieved from your codebase.
                <strong> Higher score = more relevant to the ticket.</strong>
                Uncheck chunks to exclude them from agent context.
                <span className="rag-threshold-note"> ✅ Scores above 60% are high-quality matches.</span>
              </div>
              <div className="rag-chunks-list">
                {allRagChunks.map((ch, i) => {
                  const excluded = excludedChunks.has(ch.path);
                  const pct = Math.round(ch.score * 100);
                  const quality = pct >= 70 ? 'high' : pct >= 40 ? 'medium' : 'low';
                  return (
                    <div key={i} className={`rag-chunk-card ${excluded ? 'chunk-excluded' : ''} quality-${quality}`}>
                      <div className="chunk-card-header">
                        <input
                          type="checkbox"
                          checked={!excluded}
                          onChange={() => toggleChunk(ch.path)}
                        />
                        <div className="chunk-score-bar-wrap">
                          <div className="chunk-score-fill" style={{ width: `${pct}%` }} />
                        </div>
                        <span className={`chunk-pct quality-${quality}`}>{pct}%</span>
                        <span className="chunk-label-badge">{ch.rag_label}</span>
                        <code className="chunk-path">{ch.path}</code>
                      </div>
                      {ch.name && (
                        <div className="chunk-name">
                          {ch.chunk_type && <span className="chunk-type-badge">{ch.chunk_type}</span>}
                          <strong>{ch.name}</strong>
                        </div>
                      )}
                      {ch.preview && (
                        <pre className="chunk-preview" style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', wordBreak: 'break-word', maxWidth: '100%', overflow: 'hidden' }}>{ch.preview}{ch.preview.length >= 300 ? '\n…' : ''}</pre>
                      )}
                    </div>
                  );
                })}
              </div>
              <div className="rag-panel-footer">
                <span>✅ Active: {allRagChunks.filter(c => !excludedChunks.has(c.path)).length}</span>
                <span>❌ Excluded: {excludedChunks.size}</span>
                <button className="btn btn-sm" onClick={() => setExcludedChunks(new Set())}>
                  Reset (include all)
                </button>
                <button className="btn btn-sm btn-danger"
                  onClick={() => setExcludedChunks(new Set(allRagChunks.filter(c => Math.round(c.score*100) < 40).map(c => c.path)))}>
                  Auto-exclude low quality (&lt;40%)
                </button>
              </div>
            </>
          )}
        </div>
      )}

      {/* -- Candidate Files panel (always visible when files exist) -- */}
      {workflowState.candidate_files && workflowState.candidate_files.length > 0 && (
        <div className="localization-results">
          <h3>📁 Files from RAG ({workflowState.candidate_files.length})</h3>
          <div className="file-candidates">
            {workflowState.candidate_files.map((file, i) => (
              <div key={i} className={`file-candidate ${selectedFiles.includes(file.path) ? 'selected' : ''}`}>
                <input
                  type="checkbox"
                  checked={selectedFiles.includes(file.path)}
                  onChange={() => toggleFile(file.path)}
                />
                <div className="file-info">
                  <span className="file-path">{file.path}</span>
                  {(file.confidence ?? file.score) !== undefined && (file.confidence ?? file.score) > 0 && (
                    <span className="confidence-score">
                      {((file.confidence ?? file.score) * 100).toFixed(0)}%
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
          <div className="file-actions">
            <button className="btn btn-secondary" onClick={() => setShowFileExplorer(true)}>
              + Add Files
            </button>
            <button
              className="btn btn-approve"
              disabled={selectedFiles.length === 0}
              onClick={approveFiles}
            >
              ✅ Approve {selectedFiles.length} File{selectedFiles.length !== 1 ? 's' : ''}
            </button>
          </div>
        </div>
      )}

      {/* -- Generated files -- */}
      {workflowState.generated_files && workflowState.generated_files.length > 0 && (
        <div className="generated-files">
          <h3>?? Generated / Modified Files</h3>
          <ul>
            {workflowState.generated_files.map((f, i) => (
              <li key={i}><code>{f}</code></li>
            ))}
          </ul>
        </div>
      )}

      {/* -- Structured explanation generated from live workflow state -- */}
      {workflowState.workflow_explanation && (
        <div className="generated-files human-report-panel">
          <h3>🧠 Workflow Explanation</h3>
          <div className="agent-output-panel">
            {workflowState.workflow_explanation.decision_count > 0 && (
              <p><strong>Decision Ledger Entries:</strong> {workflowState.workflow_explanation.decision_count}</p>
            )}

            <div className="agent-output-label">Summary</div>
            <p>{workflowState.workflow_explanation.summary}</p>

            <div className="agent-output-label">Why</div>
            <p>{workflowState.workflow_explanation.why}</p>

            {workflowState.workflow_explanation.decisions?.length > 0 && (
              <>
                <div className="agent-output-label">Key Decisions</div>
                <ul>
                  {workflowState.workflow_explanation.decisions.map((d, i) => (
                    <li key={i}>{d}</li>
                  ))}
                </ul>
                {workflowState.workflow_explanation.decision_debug?.length > workflowState.workflow_explanation.decisions.length && (
                  <>
                    <button
                      type="button"
                      className="btn btn-sm"
                      onClick={() => setShowDecisionDebug(prev => !prev)}
                    >
                      {showDecisionDebug ? 'Hide Decision Debug' : 'Expand Decision Debug'}
                    </button>
                    {showDecisionDebug && (
                      <ul>
                        {workflowState.workflow_explanation.decision_debug.map((d, i) => (
                          <li key={`debug-${i}`}>{d}</li>
                        ))}
                      </ul>
                    )}
                  </>
                )}
              </>
            )}

            {workflowState.workflow_explanation.evidence?.length > 0 && (
              <>
                <div className="agent-output-label">Evidence</div>
                <ul>
                  {workflowState.workflow_explanation.evidence.map((e, i) => (
                    <li key={i}>{e}</li>
                  ))}
                </ul>
              </>
            )}

            {workflowState.workflow_explanation.details?.length > 0 && (
              <>
                <div className="agent-output-label">Workflow Details</div>
                <ul>
                  {workflowState.workflow_explanation.details.map((d, i) => (
                    <li key={i}>{d}</li>
                  ))}
                </ul>
              </>
            )}

            {workflowState.workflow_explanation.confidence_trend?.length > 0 && (
              <>
                <div className="agent-output-label">Confidence Trend</div>
                <ul>
                  {workflowState.workflow_explanation.confidence_trend.map((c, i) => (
                    <li key={i}>{c}</li>
                  ))}
                </ul>
              </>
            )}

            {workflowState.workflow_explanation.active_assumptions?.length > 0 && (
              <>
                <div className="agent-output-label">Active Assumptions</div>
                <ul>
                  {workflowState.workflow_explanation.active_assumptions.map((a, i) => (
                    <li key={i}>{a}</li>
                  ))}
                </ul>
              </>
            )}

            {workflowState.workflow_explanation.invalidated_assumptions?.length > 0 && (
              <>
                <div className="agent-output-label">Invalidated Assumptions</div>
                <ul>
                  {workflowState.workflow_explanation.invalidated_assumptions.map((a, i) => (
                    <li key={i}>{a}</li>
                  ))}
                </ul>
              </>
            )}

            {workflowState.workflow_explanation.blockers?.length > 0 && (
              <>
                <div className="agent-output-label">Current Blockers</div>
                <ul>
                  {workflowState.workflow_explanation.blockers.map((b, i) => (
                    <li key={i}>{b}</li>
                  ))}
                </ul>
              </>
            )}

            <div className="agent-output-label">Next Action</div>
            <p>{workflowState.workflow_explanation.next_action}</p>
          </div>
        </div>
      )}

      {/* -- Error -- */}
      {isFailed && workflowState.error && (
        <div className="workflow-error">
          <h3>❌ Workflow Failed</h3>
          <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', wordBreak: 'break-word', maxWidth: '100%', overflow: 'hidden' }}>{workflowState.error}</pre>
        </div>
      )}

      {/* -- Complete -- */}
      {isCompleted && (
        <div className="workflow-complete">
          ✅ Workflow completed successfully. Explanation is shown above.
        </div>
      )}

      {/* -- File Explorer Modal -- */}
      {showFileExplorer && (
        <FileExplorerModal
          repoPath={repoPath}
          selectedFiles={selectedFiles}
          onToggle={toggleFile}
          onClose={() => setShowFileExplorer(false)}
        />
      )}
    </div>
  );
};

// -- File Explorer Modal -----------------------------------------------------

const FileExplorerModal = ({ repoPath, selectedFiles, onToggle, onClose }) => {
  const [fileTree, setFileTree] = useState(null);

  useEffect(() => {
    // Fetch real file tree from backend if available, else mock
    fetch(`${API}/api/file-tree?path=${encodeURIComponent(repoPath)}`)
      .then(r => r.json())
      .then(setFileTree)
      .catch(() => {
        setFileTree({
          name: repoPath.split(/[/\\]/).pop(),
          type: 'folder',
          children: [
            { name: 'src', type: 'folder', children: [
              { name: 'main', type: 'folder', children: [
                { name: 'java', type: 'folder', children: [
                  { name: 'AreaService.java', type: 'file', path: 'src/main/java/AreaService.java' },
                ] }
              ]}
            ]}
          ],
        });
      });
  }, [repoPath]);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content file-explorer" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h3>Add Files from Repository</h3>
          <button className="btn-close" onClick={onClose}>�</button>
        </div>
        <div className="modal-body">
          {fileTree
            ? <FileTreeNode node={fileTree} selectedFiles={selectedFiles} onToggle={onToggle} />
            : <p>Loading�</p>}
        </div>
        <div className="modal-footer">
          <button className="btn btn-primary" onClick={onClose}>Done</button>
        </div>
      </div>
    </div>
  );
};

const FileTreeNode = ({ node, selectedFiles, onToggle, level = 0 }) => {
  const [expanded, setExpanded] = useState(level === 0);

  if (node.type === 'file') {
    return (
      <div className="tree-file" style={{ paddingLeft: `${level * 20}px` }}>
        <input
          type="checkbox"
          checked={selectedFiles.includes(node.path)}
          onChange={() => onToggle(node.path)}
        />
        <span className="file-icon">??</span>
        <span className="file-name">{node.name}</span>
      </div>
    );
  }

  return (
    <div className="tree-folder">
      <div
        className="folder-header"
        style={{ paddingLeft: `${level * 20}px` }}
        onClick={() => setExpanded(!expanded)}
      >
        <span className="folder-icon">{expanded ? '??' : '??'}</span>
        <span className="folder-name">{node.name}</span>
      </div>
      {expanded && node.children && (
        <div className="folder-children">
          {node.children.map((child, i) => (
            <FileTreeNode
              key={i}
              node={child}
              selectedFiles={selectedFiles}
              onToggle={onToggle}
              level={level + 1}
            />
          ))}
        </div>
      )}
    </div>
  );
};

export default TransparentWorkflow;
