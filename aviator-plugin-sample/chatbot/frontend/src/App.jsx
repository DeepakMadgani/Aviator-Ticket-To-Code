import { useState, useEffect, useRef } from 'react';
import './App.css';
import TransparentWorkflow from './components/TransparentWorkflow';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8002';
const WS_BASE_URL = API_BASE_URL.replace(/^http/, 'ws');

function App() {
  // Projects state
  const [projects, setProjects] = useState([]);
  const [showAddProject, setShowAddProject] = useState(false);
  const [newProject, setNewProject] = useState({
    name: '',
    type: 'local',
    path: ''
  });

  // Chat state
  const [messages, setMessages] = useState([]);
  const [currentMessage, setCurrentMessage] = useState('');
  const [attachments, setAttachments] = useState([]);
  const [isProcessing, setIsProcessing] = useState(false);
  const [selectedProject, setSelectedProject] = useState(null);

  // Chat & task history (persisted server-side, survives restarts)
  const [chats, setChats] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [activeChatId, setActiveChatId] = useState(null);
  const [activeChatWorkflowId, setActiveChatWorkflowId] = useState(null); // workflow linked to current chat
  const [historyTab, setHistoryTab] = useState('chats'); // 'chats' | 'tasks'
  const saveTimer = useRef(null);
  const skipNextSave = useRef(false);
  const initialLoadResumed = useRef(false);
  
  // Transparent workflow state
  const [showWorkflow, setShowWorkflow] = useState(false);
  const [workflowData, setWorkflowData] = useState(null);

  // Tab state: 'chat' | 'flow' | 'changes'
  const [activeTab, setActiveTab] = useState('chat');
  const [flowStepCount, setFlowStepCount] = useState(0);

  // Changes tab state (real diffs)
  const [workflowChanges, setWorkflowChanges] = useState(null);
  const [changesLoading, setChangesLoading] = useState(false);
  const [expandedDiffs, setExpandedDiffs] = useState({});

  // Track if the current workflow is completed (for follow-up routing)
  const [workflowCompleted, setWorkflowCompleted] = useState(false);

  // Theme state: 'dark' | 'light'
  const [theme, setTheme] = useState(() => localStorage.getItem('aviator-theme') || 'dark');

  // Apply theme class to document
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('aviator-theme', theme);
  }, [theme]);

  // Live indexing progress state
  const [indexingState, setIndexingState] = useState(null);
  // { projectId, projectName, done, total, percent, currentFile, log: [], status: 'running'|'done'|'error', stats: null }
  
  const messagesEndRef = useRef(null);
  const fileInputRef = useRef(null);
  const ws = useRef(null);

  // WebSocket connection with auto-reconnect
  useEffect(() => {
    let reconnectTimer = null;

    const connect = () => {
      const socket = new WebSocket(`${WS_BASE_URL}/ws`);
      ws.current = socket;

      socket.onopen = () => {
        console.log('WebSocket connected');
      };

      socket.onmessage = (event) => {
        const data = JSON.parse(event.data);
        console.log('WebSocket message:', data);

        if (data.type === 'indexing_progress') {
          setIndexingState(prev => {
            if (!prev) return prev;
            const newLog = prev.log.slice(-199); // keep last 200 entries
            newLog.push(data.current_file);
            return {
              ...prev,
              done: data.files_scanned,
              total: data.total_files,
              percent: data.progress_percent,
              currentFile: data.current_file,
              log: newLog,
            };
          });
        } else if (data.type === 'project_status') {
          fetchProjects();
          if (data.status === 'indexed') {
            setIndexingState(prev => prev ? {
              ...prev,
              status: 'done',
              currentFile: '',
              stats: data.stats || null,
              message: data.message,
            } : prev);
          } else if (data.status === 'error') {
            setIndexingState(prev => prev ? { ...prev, status: 'error', message: data.message } : prev);
          }
        } else if (data.type === 'workflow_summary') {
          // ── Workflow completed: show rich conversational summary in Chat ──
          setWorkflowCompleted(true);
          setMessages(prev => [...prev, {
            role: 'assistant',
            content: data.summary || '✅ Workflow completed.',
            timestamp: new Date(),
            isWorkflowSummary: true,
            workflowId: data.workflow_id,
            generatedFiles: data.generated_files || [],
            workflowStatus: data.status,
            tokenUsage: data.token_usage || null,
          }]);
          // If the user was watching the live execution on the flow tab, keep them on flow so they can see completion & token usage
          setActiveTab(prev => (prev === 'flow' ? 'flow' : 'chat'));
          // Pre-load changes for the Changes tab
          if (data.workflow_id) {
            // Inline fetch (can't reference fetchWorkflowChanges in this closure)
            setChangesLoading(true);
            fetch(`${API_BASE_URL}/api/workflow/transparent/${data.workflow_id}/changes`)
              .then(r => r.ok ? r.json() : null)
              .then(d => { if (d) setWorkflowChanges(d); })
              .catch(() => {})
              .finally(() => setChangesLoading(false));
          }
        } else if (data.type === 'workflow_step') {
          // Enhancement pipeline stage events — show inline in chat
          setMessages(prev => [...prev, {
            role: 'system',
            content: `${data.message || data.phase || 'Pipeline step'} ${data.status === 'complete' ? '✓' : ''}`,
            timestamp: new Date(),
            isEnhancement: true,
            enhancementData: data.data || null,
          }]);
          if (data.workflow_id && (data.phase === 'patch_generation' || data.phase === 'validation' || data.data?.node === 'generate_code')) {
            fetch(`${API_BASE_URL}/api/workflow/transparent/${data.workflow_id}/changes`)
              .then(r => r.ok ? r.json() : null)
              .then(d => { if (d && d.changes && d.changes.length > 0) setWorkflowChanges(d); })
              .catch(() => {});
          }
        } else if (data.type && data.type !== 'ping') {
          setMessages(prev => [...prev, {
            role: 'system',
            content: data.message || JSON.stringify(data),
            timestamp: new Date()
          }]);
        }
      };

      socket.onerror = (error) => {
        console.error('WebSocket error:', error);
      };

      socket.onclose = () => {
        console.log('WebSocket closed, reconnecting in 3s...');
        reconnectTimer = setTimeout(connect, 3000);
      };
    };

    connect();

    return () => {
      clearTimeout(reconnectTimer);
      if (ws.current) ws.current.close();
    };
  }, []);

  // Scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Load projects
  useEffect(() => {
    fetchProjects();
  }, []);

  const fetchProjects = async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/api/projects`);
      const data = await response.json();
      setProjects(Array.isArray(data) ? data : (data.projects || []));
    } catch (error) {
      console.error('Error fetching projects:', error);
    }
  };

  // ── Chat & Task history ──────────────────────────────────────────────────
  const fetchHistory = async (projectId) => {
    try {
      const q = projectId ? `?project_id=${encodeURIComponent(projectId)}` : '';
      const [chatsRes, tasksRes] = await Promise.all([
        fetch(`${API_BASE_URL}/api/history/chats${q}`),
        fetch(`${API_BASE_URL}/api/history/tasks${q}`),
      ]);
      setChats(chatsRes.ok ? await chatsRes.json() : []);
      setTasks(tasksRes.ok ? await tasksRes.json() : []);
    } catch (error) {
      console.error('Error fetching history:', error);
    }
  };

  // Reload history whenever the selected project changes.
  useEffect(() => {
    fetchHistory(selectedProject?.id);
  }, [selectedProject]);

  // While a workflow runs, poll so completed tasks appear in history promptly.
  useEffect(() => {
    if (!showWorkflow) return;
    const t = setInterval(() => fetchHistory(selectedProject?.id), 5000);
    return () => clearInterval(t);
  }, [showWorkflow, selectedProject]);

  // Auto-resume the latest active task on initial page load only if it is actually running
  useEffect(() => {
    if (!initialLoadResumed.current && messages.length === 0 && !showWorkflow && tasks.length > 0 && selectedProject) {
      initialLoadResumed.current = true;
      const latestTask = [...tasks].sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0))[0];
      if (latestTask && latestTask.status === 'running') {
        setActiveChatWorkflowId(latestTask.workflow_id);
        setWorkflowData({
          projectId: selectedProject.id,
          ticketId: latestTask.ticket_id || latestTask.title || 'Recovered Task',
          ticketDescription: latestTask.description || latestTask.title || '',
          repoPath: selectedProject.path,
          attachments: [],
          existingWorkflowId: latestTask.workflow_id,
        });
        setShowWorkflow(true);
        setActiveTab('flow');
      }
    }
  }, [tasks, messages.length, showWorkflow, selectedProject]);

  // Only serializable message fields are persisted (drop File/blob objects).
  const serializeMessages = (msgs) => msgs.map(m => ({
    role: m.role,
    content: m.content,
    timestamp: (m.timestamp instanceof Date ? m.timestamp : new Date(m.timestamp)).toISOString(),
    isEnhancement: m.isEnhancement || false,
    attachments: (m.attachments || []).map(a => ({
      name: a.name, filepath: a.filepath || null, isImage: !!a.isImage,
    })),
  }));

  const saveCurrentChat = async (msgs, chatId, workflowId) => {
    if (!msgs || msgs.length === 0) return chatId;
    const targetChatId = chatId || activeChatIdRef.current;
    try {
      const res = await fetch(`${API_BASE_URL}/api/history/chats`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          chat_id: targetChatId,
          project_id: selectedProject?.id || null,
          messages: serializeMessages(msgs),
          workflow_id: workflowId || null,
        }),
      });
      if (res.ok) {
        const data = await res.json();
        if (!targetChatId) setActiveChatId(data.id);
        fetchHistory(selectedProject?.id);
        return data.id;
      }
    } catch (error) {
      console.error('Error saving chat:', error);
    }
    return targetChatId;
  };

  // Debounced auto-save whenever messages change (like AI IDE chat history).
  useEffect(() => {
    if (skipNextSave.current) { skipNextSave.current = false; return; }
    if (!messages || messages.length === 0) return;
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => { saveCurrentChat(messages, activeChatId, activeChatWorkflowId); }, 900);
    return () => { if (saveTimer.current) clearTimeout(saveTimer.current); };
  }, [messages]);

  const openChat = async (chatId) => {
    try {
      const res = await fetch(`${API_BASE_URL}/api/history/chats/${chatId}`);
      if (!res.ok) return;
      const chat = await res.json();
      skipNextSave.current = true; // loading isn't an edit
      setMessages((chat.messages || []).map(m => ({
        ...m,
        timestamp: m.timestamp ? new Date(m.timestamp) : new Date(),
      })));
      setActiveChatId(chat.id);
      setActiveChatWorkflowId(chat.workflow_id || null);

      if (chat.workflow_id && selectedProject) {
        setWorkflowData({
          projectId: selectedProject.id,
          ticketId: chat.title,
          ticketDescription: chat.title,
          repoPath: selectedProject.path,
          attachments: [],
          existingWorkflowId: chat.workflow_id,
        });
        setShowWorkflow(true);
      } else {
        setShowWorkflow(false);
      }
      setIndexingState(null);
    } catch (error) {
      console.error('Error opening chat:', error);
    }
  };

  const startNewChat = () => {
    skipNextSave.current = true;
    setMessages([]);
    setActiveChatId(null);
    setActiveChatWorkflowId(null);
    setShowWorkflow(false);
    setActiveTab('chat');
    setIndexingState(null);
    setWorkflowCompleted(false);
    setWorkflowChanges(null);
    setExpandedDiffs({});
  };

  // Open the workflow view linked to a chat's run (View Run button)
  const viewChatRun = (chat) => {
    if (!chat.workflow_id || !selectedProject) return;
    setWorkflowData({
      projectId: selectedProject.id,
      ticketId: chat.title,
      ticketDescription: chat.title,
      repoPath: selectedProject.path,
      attachments: [],
      existingWorkflowId: chat.workflow_id,
    });
    setShowWorkflow(true);
    setActiveTab('flow');
  };

  const deleteChat = async (chatId, event) => {
    event.stopPropagation();
    if (!window.confirm('Delete this conversation?')) return;
    try {
      await fetch(`${API_BASE_URL}/api/history/chats/${chatId}`, { method: 'DELETE' });
      if (activeChatId === chatId) startNewChat();
      fetchHistory(selectedProject?.id);
    } catch (error) {
      console.error('Error deleting chat:', error);
    }
  };

  const handleAddProject = async () => {
    if (!newProject.name || !newProject.path) {
      alert('Please fill in all fields');
      return;
    }

    try {
      const response = await fetch(`${API_BASE_URL}/api/projects`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: newProject.name,
          type: newProject.type,
          path: newProject.path
        })
      });

      if (response.ok) {
        setNewProject({ name: '', type: 'local', path: '' });
        setShowAddProject(false);
        fetchProjects();
      } else {
        const error = await response.json();
        alert(`Error: ${error.detail || 'Failed to add project'}`);
      }
    } catch (error) {
      console.error('Error adding project:', error);
      alert('Error adding project');
    }
  };

  const handleDeleteProject = async (projectId, event) => {
    event.stopPropagation();
    if (!window.confirm('Delete this project?')) return;
    try {
      const response = await fetch(`${API_BASE_URL}/api/projects/${projectId}`, {
        method: 'DELETE'
      });
      if (response.ok) {
        if (selectedProject?.id === projectId) setSelectedProject(null);
        if (indexingState?.projectId === projectId) setIndexingState(null);
        fetchProjects();
      } else {
        const error = await response.json();
        alert(`Error: ${error.detail || 'Failed to delete project'}`);
      }
    } catch (error) {
      console.error('Error deleting project:', error);
      alert('Error deleting project');
    }
  };

  const handleIndexProject = async (projectId, event) => {
    event.stopPropagation();
    const project = projects.find(p => p.id === projectId);

    // Show live panel immediately
    setIndexingState({
      projectId,
      projectName: project?.name || projectId,
      projectPath: project?.path || '',
      done: 0,
      total: 0,
      percent: 0,
      currentFile: 'Starting...',
      log: [],
      status: 'running',
      stats: null,
      message: '',
    });
    setShowWorkflow(false); // switch to chat/indexing view

    try {
      const response = await fetch(`${API_BASE_URL}/api/projects/${projectId}/index`, {
        method: 'POST'
      });
      if (!response.ok) {
        const error = await response.json();
        setIndexingState(prev => prev ? { ...prev, status: 'error', message: error.detail || 'Indexing failed' } : prev);
      }
      // Success handled via WebSocket project_status message
    } catch (error) {
      console.error('Error indexing project:', error);
      setIndexingState(prev => prev ? { ...prev, status: 'error', message: error.message } : prev);
    }
  };

  // Helper to categorize and sequentially number attachments by type
  const getAttachmentMeta = (att, index, allAttachments) => {
    const isImg = att.isImage || (att.name && /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(att.name));
    const isLog = att.name && /\.(log|txt|out)$/i.test(att.name) && (
      att.name.toLowerCase().includes('log') || 
      att.name.toLowerCase().includes('err') || 
      att.name.toLowerCase().includes('trace') ||
      att.name.toLowerCase().includes('console')
    );
    const category = isImg ? 'image' : isLog ? 'log' : 'file';

    let count = 0;
    for (let i = 0; i <= index; i++) {
      const item = allAttachments[i];
      if (!item) continue;
      const itemIsImg = item.isImage || (item.name && /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(item.name));
      const itemIsLog = item.name && /\.(log|txt|out)$/i.test(item.name) && (
        item.name.toLowerCase().includes('log') || 
        item.name.toLowerCase().includes('err') || 
        item.name.toLowerCase().includes('trace') ||
        item.name.toLowerCase().includes('console')
      );
      const itemCat = itemIsImg ? 'image' : itemIsLog ? 'log' : 'file';
      if (itemCat === category) {
        count++;
      }
    }

    const label = category === 'image' ? `Image ${count}` : category === 'log' ? `Log ${count}` : `File ${count}`;
    return { category, count, label };
  };

  // Upload helper
  const uploadAttachment = async (file) => {
    const formData = new FormData();
    formData.append('file', file);
    try {
      const res = await fetch(`${API_BASE_URL}/api/upload`, {
        method: 'POST',
        body: formData,
      });
      if (res.ok) {
        const data = await res.json();
        return data.filepath;
      } else {
        console.error('Failed to upload file');
        return null;
      }
    } catch (err) {
      console.error('Upload error:', err);
      return null;
    }
  };

  const handleFileSelect = async (e) => {
    const files = Array.from(e.target.files || []);
    if (!files.length) return;
    for (const file of files) {
      const isImage = file.type.startsWith('image/');
      const previewUrl = isImage ? URL.createObjectURL(file) : null;
      const newAtt = {
        file,
        name: file.name,
        previewUrl,
        isImage,
        filepath: null,
        isUploading: true,
      };
      setAttachments(prev => [...prev, newAtt]);
      const serverPath = await uploadAttachment(file);
      setAttachments(prev => prev.map(a => a.file === file ? { ...a, filepath: serverPath, isUploading: false } : a));
    }
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const handlePaste = async (e) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    for (let i = 0; i < items.length; i++) {
      if (items[i].type.indexOf('image') !== -1) {
        const file = items[i].getAsFile();
        if (file) {
          e.preventDefault();
          const previewUrl = URL.createObjectURL(file);
          const fileName = `screenshot_${Date.now()}.png`;
          const namedFile = new File([file], fileName, { type: file.type });
          const newAtt = {
            file: namedFile,
            name: fileName,
            previewUrl,
            isImage: true,
            filepath: null,
            isUploading: true,
          };
          setAttachments(prev => [...prev, newAtt]);
          const serverPath = await uploadAttachment(namedFile);
          setAttachments(prev => prev.map(a => a.file === namedFile ? { ...a, filepath: serverPath, isUploading: false } : a));
        }
      }
    }
  };

  const removeAttachment = (indexToRemove) => {
    setAttachments(prev => prev.filter((_, idx) => idx !== indexToRemove));
  };

  // Fetch changes/diffs for the Changes tab
  const fetchWorkflowChanges = async (wfId) => {
    if (!wfId) return;
    setChangesLoading(true);
    try {
      const res = await fetch(`${API_BASE_URL}/api/workflow/transparent/${wfId}/changes`);
      if (res.ok) {
        const data = await res.json();
        setWorkflowChanges(data);
      }
    } catch (err) {
      console.error('Error fetching changes:', err);
    } finally {
      setChangesLoading(false);
    }
  };

  const handleSendMessage = async () => {
    if ((!currentMessage.trim() && attachments.length === 0) || isProcessing) return;

    const userMessage = currentMessage.trim() || (attachments.length > 0 ? "Analyze attached screenshot/file and provide solution" : "");
    const attachedPaths = attachments.map(a => a.filepath).filter(Boolean);
    const currentAtts = [...attachments];

    setCurrentMessage('');
    setAttachments([]);
    
    // Add user message to chat
    setMessages(prev => [...prev, {
      role: 'user',
      content: userMessage,
      attachments: currentAtts,
      timestamp: new Date()
    }]);

    setIsProcessing(true);

    try {
      // ── Follow-up question about a completed workflow ──
      // If a workflow is completed and user sends a message, route to the
      // conversational chat endpoint (LLM answers using workflow context)
      if (workflowCompleted && activeChatWorkflowId) {
        setMessages(prev => [...prev, {
          role: 'assistant',
          content: '🤔 Thinking...',
          timestamp: new Date(),
          isThinking: true,
        }]);

        try {
          const res = await fetch(`${API_BASE_URL}/api/workflow/chat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              workflow_id: activeChatWorkflowId,
              message: userMessage,
              project_id: selectedProject?.id || null,
            }),
          });

          // Remove "Thinking..." placeholder
          setMessages(prev => prev.filter(m => !m.isThinking));

          if (res.ok) {
            const data = await res.json();
            setMessages(prev => [...prev, {
              role: 'assistant',
              content: data.reply || 'No response generated.',
              timestamp: new Date(),
              isFollowUp: true,
            }]);
          } else {
            setMessages(prev => [...prev, {
              role: 'assistant',
              content: '❌ Could not get a response. Please try again.',
              timestamp: new Date(),
            }]);
          }
        } catch (chatErr) {
          setMessages(prev => prev.filter(m => !m.isThinking));
          setMessages(prev => [...prev, {
            role: 'assistant',
            content: `❌ Error: ${chatErr.message}`,
            timestamp: new Date(),
          }]);
        }
        setIsProcessing(false);
        return;
      }

      // ── Check if this is a ticket — any task-like message when project is selected ──
      const lower = userMessage.toLowerCase();
      const isTicketFile = selectedProject && (
        attachedPaths.length > 0 ||
        userMessage.includes('.py') || userMessage.includes('.cs') ||
        userMessage.includes('.java') || userMessage.includes('_') ||
        lower.includes('ticket') || lower.includes('implement') ||
        lower.includes('fix') || lower.includes('add') ||
        lower.includes('update') || lower.includes('change') ||
        lower.includes('create') || lower.includes('modify') ||
        lower.includes('remove') || lower.includes('delete') ||
        lower.includes('should') || lower.includes('need') ||
        lower.includes('show') || lower.includes('display') ||
        lower.includes('migrate') || lower.includes('refactor') ||
        lower.includes('endpoint') || lower.includes('api') ||
        userMessage.length > 30  // any reasonably long message = treat as ticket
      );

      if (isTicketFile && selectedProject) {
        // Reset workflow completion state for new runs
        setWorkflowCompleted(false);
        setWorkflowChanges(null);

        // Start transparent workflow
        setMessages(prev => [...prev, {
          role: 'assistant',
          content: `🚀 Starting transparent workflow for: ${userMessage}${attachedPaths.length ? `\n📸 Attached ${attachedPaths.length} file(s) for visual / runtime analysis.` : ''}\n\nYou'll see every step and can approve at key checkpoints.`,
          timestamp: new Date()
        }]);

        // Show transparent workflow UI
        setWorkflowData({
          projectId: selectedProject.id,
          ticketId: userMessage,   // full description; backend auto-generates ID
          ticketDescription: userMessage,
          repoPath: selectedProject.path,
          attachments: attachedPaths,
          writableFiles: [],
          forbiddenFiles: [],
          existingWorkflowId: null, // new run; onWorkflowStarted will fill this in
        });
        setShowWorkflow(true);
        setActiveTab('flow');

      } else {
        // Regular chat message
        setMessages(prev => [...prev, {
          role: 'assistant',
          content: `I received: "${userMessage}"\n\n${selectedProject ? '✅ Project selected' : '⚠️ Please select a project first'}\n\nTo start a transparent workflow, enter a ticket description or attach a screenshot/log file:\n• "Implement user authentication feature"\n• "Fix null pointer exception in PaymentService"\n• "Add logging to database queries"\n• Or click 📎 / paste an image to analyze UI bugs`,
          timestamp: new Date()
        }]);
      }
    } catch (error) {
      console.error('Error:', error);
      setMessages(prev => [...prev, {
        role: 'system',
        content: `❌ Error: ${error.message}`,
        timestamp: new Date()
      }]);
    } finally {
      setIsProcessing(false);
    }
  };

  const handleKeyPress = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  // Collect workflow IDs that are already linked to a chat
  const chatWorkflowIds = new Set(chats.map(c => c.workflow_id).filter(Boolean));
  // Filter out tasks that are already represented by a chat
  const standaloneTasks = tasks
    .filter(t => !chatWorkflowIds.has(t.workflow_id))
    .map(t => ({...t, isTask: true, id: t.workflow_id}));

  const combinedHistory = [...chats, ...standaloneTasks]
    .sort((a, b) => new Date(b.updated_at || b.created_at || 0) - new Date(a.updated_at || a.created_at || 0));

  return (
    <div className="app">
      {/* Left Panel - Projects */}
      <div className="left-panel">
        <div className="panel-header">
          <h2>📁 Projects</h2>
          <button 
            className="add-btn"
            onClick={() => setShowAddProject(!showAddProject)}
          >
            {showAddProject ? '✕' : '+'}
          </button>
        </div>

        {showAddProject && (
          <div className="add-project-form">
            <input
              type="text"
              placeholder="Project name"
              value={newProject.name}
              onChange={(e) => setNewProject({ ...newProject, name: e.target.value })}
            />
            <select
              value={newProject.type}
              onChange={(e) => setNewProject({ ...newProject, type: e.target.value })}
            >
              <option value="local">Local</option>
              <option value="git">Git</option>
            </select>
            <input
              type="text"
              placeholder={newProject.type === 'local' ? 'Local path (C:\\...)' : 'Git URL'}
              value={newProject.path}
              onChange={(e) => setNewProject({ ...newProject, path: e.target.value })}
            />
            <button onClick={handleAddProject}>Add Project</button>
          </div>
        )}

        <div className="projects-list">
          {projects.length === 0 ? (
            <div className="empty-state">
              <p>No projects yet</p>
              <p className="hint">Click + to add one</p>
            </div>
          ) : (
            projects.map(project => (
              <div
                key={project.id}
                className={`project-item ${selectedProject?.id === project.id ? 'selected' : ''}`}
                onClick={() => setSelectedProject(project)}
              >
                <div className="project-icon">
                  {project.type === 'local' ? '📂' : '🔗'}
                </div>
                <div className="project-info">
                  <div className="project-name">{project.name}</div>
                  <div className="project-path">{project.path}</div>
                  <div className="project-status">
                    {project.status === 'indexed' && '✅ Indexed'}
                    {project.status === 'indexing' && '⏳ Indexing...'}
                    {project.status === 'ready' && '📊 Ready (Not Indexed)'}
                    {project.status === 'pending' && '⏸️ Pending'}
                    {project.status === 'error' && '❌ Error'}
                  </div>
                  {project.status !== 'indexed' && project.status !== 'indexing' && (
                    <button
                      className="index-btn"
                      onClick={(e) => handleIndexProject(project.id, e)}
                      title="Index this project before entering tickets"
                    >
                      🔍 Index Project
                    </button>
                  )}
                </div>
                <button
                  className="delete-project-btn"
                  onClick={(e) => handleDeleteProject(project.id, e)}
                  title="Remove project"
                >
                  ✕
                </button>
              </div>
            ))
          )}
        </div>

        {/* Chat & Task History — combined list */}
        <div className="history-panel">
          <div className="history-header">
            <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-secondary)', letterSpacing: '0.05em', textTransform: 'uppercase' }}>History {combinedHistory.length > 0 && <span className="history-count">{combinedHistory.length}</span>}</span>
            <button className="new-chat-btn" onClick={startNewChat} title="Start a new chat">
              + New
            </button>
          </div>

          <div className="history-list">
            {combinedHistory.length === 0 ? (
              <div className="history-empty">No previous history yet</div>
            ) : (
              combinedHistory.map(item => (
                <div
                  key={item.id}
                  className={`history-item ${(activeChatId === item.id || activeChatWorkflowId === item.workflow_id) ? 'active' : ''}`}
                  onClick={() => {
                    if (item.isTask) {
                      setActiveChatId(null);
                      setActiveChatWorkflowId(item.workflow_id);
                      setWorkflowData({
                        projectId: selectedProject?.id,
                        ticketId: item.ticket_id || item.title || 'Task',
                        ticketDescription: item.description || item.title || '',
                        repoPath: selectedProject?.path,
                        attachments: [],
                        existingWorkflowId: item.workflow_id,
                      });
                      setShowWorkflow(true);
                      setActiveTab('flow');
                      setMessages([]); // clear chat view for pure task
                    } else {
                      openChat(item.id);
                      setActiveTab('chat');
                    }
                  }}
                  title={item.title || item.ticket_id}
                >
                  <div className="history-item-main">
                    <div className="history-item-title">
                      {item.isTask ? <span style={{ opacity: 0.5 }}>⚙️</span> : (item.workflow_id ? <span style={{ opacity: 0.5 }}>🔄</span> : <span style={{ opacity: 0.5 }}>💬</span>)}
                      {item.title || item.ticket_id}
                    </div>
                    <div className="history-item-meta">
                      {item.isTask ? `Status: ${item.status}` : `${item.message_count} msg`} · {new Date(item.updated_at || item.created_at).toLocaleString()}
                    </div>
                  </div>
                  <button
                    className="history-delete-btn"
                    onClick={(e) => {
                      e.stopPropagation();
                      if (item.isTask) {
                        fetch(`${API_BASE_URL}/api/history/tasks/${item.workflow_id}`, { method: 'DELETE' })
                          .then(() => fetchHistory(selectedProject?.id));
                      } else {
                        deleteChat(item.id, e);
                      }
                    }}
                    title="Delete item"
                  >
                    ✕
                  </button>
                </div>
              ))
            )}
          </div>
        </div>
      </div>

      {/* Right Panel - Tabbed Content Area */}
      <div className="right-panel">
        <div className="panel-header">
          <h2>🤖 Aviator</h2>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            {selectedProject && (
              <span className="selected-project-badge">
                {selectedProject.name}
              </span>
            )}
            <button
              className="theme-toggle-btn"
              onClick={() => setTheme(t => t === 'dark' ? 'light' : 'dark')}
              title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
            >
              {theme === 'dark' ? '\u2600' : '\u263D'}
            </button>
          </div>
        </div>

        {/* ── Tab Bar ──────────────────────────────────────── */}
        <div className="main-tab-bar">
          <button
            className={`main-tab ${activeTab === 'chat' ? 'active' : ''}`}
            onClick={() => setActiveTab('chat')}
          >
            💬 Chat
          </button>
          <button
            className={`main-tab ${activeTab === 'flow' ? 'active' : ''}`}
            onClick={() => setActiveTab('flow')}
          >
            🔄 Flow
            {flowStepCount > 0 && <span className="tab-badge">{flowStepCount}</span>}
          </button>
          <button
            className={`main-tab ${activeTab === 'changes' ? 'active' : ''}`}
            onClick={() => setActiveTab('changes')}
          >
            📝 Changes
          </button>
        </div>

        {/* ── Tab Content ──────────────────────────────── */}
        <div className="tab-content">

        {/* ═══ FLOW TAB ═══ */}
        {activeTab === 'flow' && workflowData ? (
          <TransparentWorkflow
            key={workflowData.existingWorkflowId || workflowData.ticketId || 'current-flow'}
            projectId={workflowData.projectId}
            ticketId={workflowData.ticketId}
            ticketDescription={workflowData.ticketDescription}
            repoPath={workflowData.repoPath}
            attachments={workflowData.attachments || []}
            writableFiles={workflowData.writableFiles || []}
            forbiddenFiles={workflowData.forbiddenFiles || []}
            existingWorkflowId={workflowData.existingWorkflowId || null}
            onWorkflowStarted={(wfId) => {
              setWorkflowData(prev => prev ? { ...prev, existingWorkflowId: wfId } : prev);
              setActiveChatWorkflowId(wfId);
              saveCurrentChat(messages, activeChatId, wfId);
            }}
            onWorkflowStopped={(stoppedWfId) => {
              // Clear workflow data so the user can start fresh
              setWorkflowData(null);
              setActiveChatWorkflowId(null);
              // Switch to chat tab so user can type a new ticket
              setActiveTab('chat');
            }}
          />
        ) : activeTab === 'flow' && !workflowData ? (
          <div className="changes-tab-empty">
            <div className="empty-icon">🔄</div>
            <h3>No Workflow Running</h3>
            <p>Submit a ticket in the Chat tab or click "View Run" on a previous chat to see the workflow flow here.</p>
          </div>

        ) : activeTab === 'changes' ? (
          workflowChanges && workflowChanges.files && workflowChanges.files.length > 0 ? (
            <div className="changes-panel">
              <div className="changes-header">
                <h3>📝 Code Changes</h3>
                <span className="changes-count">{workflowChanges.files.length} file(s) changed</span>
              </div>
              <div className="changes-file-list">
                {workflowChanges.files.map((file, idx) => {
                  const basename = file.path.split('/').pop() || file.path.split('\\').pop() || file.path;
                  const isExpanded = expandedDiffs[idx];
                  return (
                    <div key={idx} className={`diff-file-block ${file.status}`}>
                      <div
                        className="diff-file-header"
                        onClick={() => setExpandedDiffs(prev => ({ ...prev, [idx]: !prev[idx] }))}
                      >
                        <span className="diff-expand-icon">{isExpanded ? '▼' : '▶'}</span>
                        <span className={`diff-status-badge ${file.status}`}>
                          {file.status === 'new' ? 'NEW' : 'MOD'}
                        </span>
                        <span className="diff-file-name">{basename}</span>
                        <span className="diff-file-path">{file.path}</span>
                      </div>
                      {isExpanded && (
                        <div className="diff-content">
                          {file.diff ? (
                            <pre className="diff-pre">
                              {file.diff.split('\n').map((line, li) => {
                                let cls = 'diff-line';
                                if (line.startsWith('+') && !line.startsWith('+++')) cls += ' diff-add';
                                else if (line.startsWith('-') && !line.startsWith('---')) cls += ' diff-remove';
                                else if (line.startsWith('@@')) cls += ' diff-hunk';
                                return <div key={li} className={cls}>{line}</div>;
                              })}
                            </pre>
                          ) : (
                            <div className="diff-no-content">No diff available for this file.</div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          ) : changesLoading ? (
            <div className="changes-tab-empty">
              <div className="empty-icon">⏳</div>
              <h3>Loading Changes...</h3>
            </div>
          ) : (
            <div className="changes-tab-empty">
              <div className="empty-icon">📝</div>
              <h3>Code Changes</h3>
              <p>After a ticket run completes, file diffs will appear here.</p>
            </div>
          )

        ) : indexingState ? (
          <div className="indexing-panel">
            <div className="indexing-header">
              <span className="indexing-title">
                {indexingState.status === 'running' ? '⚙️ Indexing' :
                 indexingState.status === 'done' ? '✅ Indexing Complete' : '❌ Indexing Failed'} — {indexingState.projectName}
              </span>
              {indexingState.status !== 'running' && (
                <button className="indexing-close-btn" onClick={() => setIndexingState(null)}>✕ Close</button>
              )}
            </div>

            {/* Progress bar */}
            <div className="indexing-progress-bar-wrap">
              <div
                className={`indexing-progress-bar ${indexingState.status}`}
                style={{ width: `${indexingState.status === 'done' ? 100 : indexingState.percent}%` }}
              />
            </div>
            <div className="indexing-progress-label">
              {indexingState.status === 'running'
                ? `${indexingState.done} / ${indexingState.total || '?'} files (${indexingState.percent}%)`
                : indexingState.status === 'done'
                ? `Completed — ${indexingState.done} files processed`
                : indexingState.message}
            </div>

            {/* Current file */}
            {indexingState.status === 'running' && (
              <div className="indexing-current-file">
                📄 <strong>{indexingState.currentFile}</strong>
              </div>
            )}

            {/* Final stats */}
            {indexingState.status === 'done' && (
              <div className="indexing-stats">
                <div className="stat-row">📁 <strong>Project:</strong> {indexingState.projectPath}</div>
                <div className="stat-row">💾 <strong>Index stored at:</strong> {indexingState.projectPath}\.aviator\index.db</div>
                {indexingState.stats && <>
                  <div className="stat-row">📂 <strong>Files scanned:</strong> {indexingState.stats.files_scanned}</div>
                  <div className="stat-row">✅ <strong>Files parsed:</strong> {indexingState.stats.files_parsed}</div>
                  <div className="stat-row">❌ <strong>Files failed:</strong> {indexingState.stats.files_failed}</div>
                  <div className="stat-row">🔤 <strong>Symbols extracted:</strong> {indexingState.stats.symbols}</div>
                  <div className="stat-row">🔗 <strong>Relationships (edges):</strong> {indexingState.stats.edges}</div>
                  {indexingState.stats.resolved_calls !== undefined && (
                    <div className="stat-row">🎯 <strong>Method→Method calls resolved:</strong> {indexingState.stats.resolved_calls} (exact call graph)</div>
                  )}
                  {indexingState.stats.vector_chunks !== undefined && (
                    <div className="stat-row">🧠 <strong>RAG vector chunks:</strong> {indexingState.stats.vector_chunks} (ready for semantic search)</div>
                  )}
                  {indexingState.stats.neo4j_nodes !== undefined && (
                    <div className="stat-row">🌐 <strong>Neo4j nodes:</strong> {indexingState.stats.neo4j_nodes}</div>
                  )}
                  {indexingState.stats.neo4j_edges !== undefined && (
                    <div className="stat-row">🔀 <strong>Neo4j relationships:</strong> {indexingState.stats.neo4j_edges} (call chains, inheritance)</div>
                  )}
                </>}
              </div>
            )}

            {/* Scrollable file log */}
            <div className="indexing-log-header">Files processed ({indexingState.log.length}):</div>
            <div className="indexing-log">
              {indexingState.log.map((f, i) => (
                <div key={i} className="indexing-log-entry">✔ {f}</div>
              ))}
              {indexingState.status === 'running' && <div className="indexing-log-pulse">…</div>}
            </div>
          </div>
        ) : (
          <>
            <div className="messages-container">
              {messages.length === 0 ? (
                <div className="welcome-message">
                  <h3>✨ Welcome to Aviator</h3>
                  <p>Autonomous Ticket-to-Code Engineering System</p>
                  
                  <div className="instructions">
                    <h4>Quick Start</h4>
                    <ol>
                      <li>Select a project from the left sidebar</li>
                      <li>Describe a bug fix or feature implementation</li>
                      <li>Optionally click 📎 to attach a screenshot, UI mockup, or log file (or paste with Ctrl+V)</li>
                      <li>Watch the transparent workflow analyze, plan, code, and verify in real time</li>
                    </ol>
                  </div>

                  <div className="feature-highlights">
                    <h4>💡 Powered by</h4>
                    <ul>
                      <li>Multi-agent AI pipeline with evidence collection</li>
                      <li>Ripgrep, RAG, SQLite, Symbol Lookup search tools</li>
                      <li>Automated SEARCH/REPLACE code patching</li>
                      <li>Build verification & self-healing</li>
                    </ul>
                  </div>
                </div>

              ) : (
                messages.filter(m => !m.isThinking).map((msg, idx) => (
                  <div key={idx} className={`message ${msg.role}${msg.isWorkflowSummary ? ' workflow-summary' : ''}${msg.isFollowUp ? ' follow-up' : ''}`}>
                    <div className="message-header">
                      <span className="message-role">
                        {msg.role === 'user' ? '👤 You' : 
                        msg.role === 'assistant' ? '🤖 Aviator' : 
                        '⚙️ System'}
                      </span>
                      <span className="message-time">
                        {msg.timestamp instanceof Date ? msg.timestamp.toLocaleTimeString() : new Date(msg.timestamp).toLocaleTimeString()}
                      </span>
                      {msg.isWorkflowSummary && (
                        <div style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
                          <span className="message-badge summary-badge">
                            {msg.workflowStatus === 'completed' ? '✅ Completed' : '❌ Failed'}
                          </span>
                          {msg.tokenUsage && (
                            <span className="message-badge" style={{ background: 'rgba(139, 92, 246, 0.2)', color: '#c4b5fd', border: '1px solid rgba(139, 92, 246, 0.4)' }}>
                              🪙 {((msg.tokenUsage.total_tokens || 0)).toLocaleString()} tokens ({msg.tokenUsage.llm_calls || 0} calls)
                            </span>
                          )}
                        </div>
                      )}
                    </div>
                    <div className="message-content">
                      {msg.attachments && msg.attachments.length > 0 && (
                        <div className="message-attachments">
                          {msg.attachments.map((att, aIdx) => {
                            const meta = getAttachmentMeta(att, aIdx, msg.attachments);
                            return (
                              <div key={aIdx} className="message-attachment-item">
                                <div className="message-attachment-tag">
                                  <span className={`att-order-badge badge-${meta.category}`}>{meta.label}</span>
                                  <span className="att-name-label">{att.name}</span>
                                </div>
                                {att.isImage ? (
                                  <img src={att.previewUrl} alt={att.name} className="attached-img-bubble" />
                                ) : (
                                  <span className="attached-file-badge">{meta.category === 'log' ? '📋' : '📄'} {att.name}</span>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      )}
                      {/* Rich markdown-style rendering for workflow summaries */}
                      {(typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content, null, 2) || '').split('\n').map((line, i) => {
                        // Bold: **text**
                        let rendered = line.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
                        // Italic: *text*
                        rendered = rendered.replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, '<em>$1</em>');
                        // Inline code: `text`
                        rendered = rendered.replace(/`([^`]+)`/g, '<code>$1</code>');
                        // Blockquote: > text
                        if (rendered.startsWith('&gt; ') || rendered.startsWith('> ')) {
                          const quoteText = rendered.replace(/^(&gt;|>) /, '');
                          return <blockquote key={i} className="chat-blockquote" dangerouslySetInnerHTML={{ __html: quoteText }} />;
                        }
                        // Bullet: • or - text
                        if (/^\s*(•|-)\s/.test(rendered)) {
                          return <div key={i} className="chat-bullet" dangerouslySetInnerHTML={{ __html: rendered }} />;
                        }
                        return <div key={i} dangerouslySetInnerHTML={{ __html: rendered }} />;
                      })}
                      {/* Show "View Changes" button for workflow summary messages */}
                      {msg.isWorkflowSummary && msg.generatedFiles && msg.generatedFiles.length > 0 && (
                        <div className="summary-actions">
                          <button
                            className="view-changes-btn"
                            onClick={() => {
                              if (msg.workflowId) fetchWorkflowChanges(msg.workflowId);
                              setActiveTab('changes');
                            }}
                          >
                            📝 View Changes ({msg.generatedFiles.length} files)
                          </button>
                          <button
                            className="view-flow-btn"
                            onClick={() => setActiveTab('flow')}
                          >
                            🔄 View Flow
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                ))
              )}
              <div ref={messagesEndRef} />
            </div>

            <div className="input-box-wrapper">
              {attachments.length > 0 && (
                <div className="attachment-preview-bar">
                  {attachments.map((att, idx) => {
                    const meta = getAttachmentMeta(att, idx, attachments);
                    return (
                      <div key={idx} className="attachment-chip">
                        <span className={`chip-badge badge-${meta.category}`}>{meta.label}</span>
                        {att.isImage ? (
                          <img src={att.previewUrl} alt={att.name} className="chip-img" />
                        ) : (
                          <span className="chip-icon">{meta.category === 'log' ? '📋' : '📄'}</span>
                        )}
                        <span className="chip-name">{att.name}</span>
                        {att.isUploading ? (
                          <span className="chip-spinner">⏳</span>
                        ) : (
                          <button type="button" className="chip-remove-btn" onClick={() => removeAttachment(idx)}>✕</button>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
              <div className="input-container">
                <button
                  type="button"
                  className="attach-btn"
                  onClick={() => fileInputRef.current?.click()}
                  title="Attach screenshot or log file (or paste with Ctrl+V)"
                  disabled={!selectedProject || isProcessing}
                >
                  📎
                </button>
                <input
                  type="file"
                  ref={fileInputRef}
                  style={{ display: 'none' }}
                  multiple
                  accept="image/*,.log,.txt,.json,.yml,.yaml,.py,.java,.cs"
                  onChange={handleFileSelect}
                />
                <textarea
                  value={currentMessage}
                  onChange={(e) => setCurrentMessage(e.target.value)}
                  onKeyDown={handleKeyPress}
                  onPaste={handlePaste}
                  placeholder={selectedProject ? 
                    "Describe your task, or paste screenshot (Ctrl+V) / click 📎 to attach..." : 
                    "Please select a project first..."}
                  disabled={!selectedProject || isProcessing}
                  rows={3}
                />
                <button
                  onClick={handleSendMessage}
                  disabled={(!currentMessage.trim() && attachments.length === 0) || !selectedProject || isProcessing}
                  className="send-btn"
                >
                  {isProcessing ? '⏳' : '▶'}
                </button>
              </div>
            </div>
          </>
        )}
        </div>{/* end tab-content */}
      </div>
    </div>
  );
}

export default App;
