import React, { useState, useEffect, useRef, useCallback } from 'react';
import axios from 'axios';
import { ChatWebSocket } from './websocket';
import './App.css';

function App() {
  const [inputValue, setInputValue] = useState('');
  const [messages, setMessages] = useState([]);
  const [chatHistory, setChatHistory] = useState([]);
  const [currentChatId, setCurrentChatId] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);
  const [error, setError] = useState(null);
  const [wsStatus, setWsStatus] = useState('disconnected');
  
  // Processing status state
  const [processingStatus, setProcessingStatus] = useState({
    status: 'idle',
    progress: 0,
    current_step: '',
    friendly_message: '',
    is_ready: false,
    is_processing: false,
    is_failed: false,
    has_error: false,
    error_message: null,
    document_id: null,
    started_at: null,
    completed_at: null
  });

  // Crawled URLs state
  const [crawledUrls, setCrawledUrls] = useState([]);
  const [showCrawledUrls, setShowCrawledUrls] = useState(true);
  const [crawlStats, setCrawlStats] = useState({
    total: 0,
    completed: 0,
    failed: 0,
    pending: 0,
    processing: 0
  });
  
  const messagesEndRef = useRef(null);
  const inputRef = useRef(null);
  const websocketRef = useRef(null);
  const reconnectTimerRef = useRef(null);
  const reconnectAttemptsRef = useRef(0);
  const MAX_RECONNECT_ATTEMPTS = 5;
  const RECONNECT_DELAY = 3000;
  
  const API_URL = 'http://localhost:8000';

  // Helper functions
  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  const fetchChatHistory = async () => {
    try {
      const response = await axios.get(`${API_URL}/api/chats`);
      console.log('Chat history:', response.data);
      setChatHistory(response.data);
    } catch (error) {
      console.error('Error fetching chat history:', error);
      setError('Failed to load chat history');
    }
  };

  // Deduplicate URLs helper - keep the latest status
  const deduplicateUrls = (urls) => {
    if (!urls || !Array.isArray(urls)) return [];
    const urlMap = new Map();
    urls.forEach(url => {
      const existing = urlMap.get(url.url);
      if (!existing) {
        urlMap.set(url.url, url);
      } else {
        // Priority: completed > processing > pending > failed
        const statusPriority = { 'completed': 4, 'processing': 3, 'pending': 2, 'failed': 1 };
        const existingPriority = statusPriority[existing.status] || 0;
        const newPriority = statusPriority[url.status] || 0;
        if (newPriority > existingPriority) {
          urlMap.set(url.url, url);
        }
      }
    });
    return Array.from(urlMap.values());
  };

  const fetchCrawledUrls = async (chatId) => {
    if (!chatId) return;
    try {
      const response = await axios.get(`${API_URL}/api/chats/${chatId}/crawled-urls`);
      console.log('Crawled URLs response:', response.data);
      
      const dedupedUrls = deduplicateUrls(response.data.urls || []);
      setCrawledUrls(dedupedUrls);
      setCrawlStats({
        total: response.data.total || 0,
        completed: response.data.completed || 0,
        failed: response.data.failed || 0,
        pending: response.data.pending || 0,
        processing: response.data.processing || 0
      });
      console.log('Set crawled URLs:', dedupedUrls.length);
    } catch (error) {
      console.error('Error fetching crawled URLs:', error);
    }
  };

  const formatDate = (dateString) => {
    if (!dateString) return 'Just now';
    try {
      const date = new Date(dateString);
      if (isNaN(date.getTime())) return 'Just now';
      const now = new Date();
      const diff = now - date;
      if (diff < 0) return 'Just now';
      if (diff < 60000) return 'Just now';
      if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
      if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
      if (diff < 604800000) return `${Math.floor(diff / 86400000)}d ago`;
      return date.toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
        year: date.getFullYear() !== now.getFullYear() ? 'numeric' : undefined
      });
    } catch {
      return 'Just now';
    }
  };

  const formatTime = (timestamp) => {
    if (!timestamp) return '';
    try {
      const date = new Date(timestamp);
      return date.toLocaleTimeString();
    } catch {
      return '';
    }
  };

  const truncateText = (text, maxLength = 50) => {
    if (!text) return '';
    if (text.length <= maxLength) return text;
    return text.substring(0, maxLength) + '...';
  };

  const renderMessageContent = (content) => {
    if (!content) return null;
    const lines = content.split('\n');
    return lines.map((line, i) => (
      <React.Fragment key={i}>
        {line}
        {i < lines.length - 1 && <br />}
      </React.Fragment>
    ));
  };

  const displayChatId = (chatId) => {
    if (!chatId) return 'New Conversation';
    const parts = chatId.split('-');
    if (parts.length === 5) {
      return `${parts[0]}-${parts[1]}-...-${parts[4]}`;
    }
    return chatId.substring(0, 8) + '...';
  };

  // Status helper functions
  const getStatusEmoji = (status) => {
    const map = {
      'idle': '⏸️',
      'pending': '⏳',
      'crawling': '🕷️',
      'processing': '⚙️',
      'chunking': '📄',
      'embedding': '🧠',
      'completed': '✅',
      'answered': '💬',
      'failed': '❌'
    };
    return map[status] || '📊';
  };

  const getStatusLabel = (status) => {
    const map = {
      'idle': 'Idle',
      'pending': 'Pending',
      'crawling': 'Crawling',
      'processing': 'Processing',
      'chunking': 'Chunking',
      'embedding': 'Embedding',
      'completed': 'Complete',
      'answered': 'Answered',
      'failed': 'Failed'
    };
    return map[status] || status;
  };

  const getStatusColor = (status) => {
    const map = {
      'idle': '#8e8ea0',
      'pending': '#f59e0b',
      'crawling': '#2196F3',
      'processing': '#3F51B5',
      'chunking': '#009688',
      'embedding': '#9C27B0',
      'completed': '#4CAF50',
      'answered': '#8BC34A',
      'failed': '#f44336'
    };
    return map[status] || '#8e8ea0';
  };

  // WebSocket status indicator
  const getWsStatusIndicator = () => {
    switch (wsStatus) {
      case 'connected':
        return <span className="ws-status connected" title="Connected">●</span>;
      case 'connecting':
        return <span className="ws-status connecting" title="Connecting...">◐</span>;
      case 'error':
        return <span className="ws-status error" title="Connection error">●</span>;
      default:
        return <span className="ws-status disconnected" title="Disconnected">○</span>;
    }
  };

  // Load chat history on mount
  useEffect(() => {
    fetchChatHistory();
  }, []);

  // Scroll to bottom when messages change
  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  // Fetch crawled URLs when chat changes
  useEffect(() => {
    if (currentChatId) {
      fetchCrawledUrls(currentChatId);
    } else {
      setCrawledUrls([]);
      setCrawlStats({ total: 0, completed: 0, failed: 0, pending: 0, processing: 0 });
    }
  }, [currentChatId]);

  // Clean up WebSocket on unmount
  useEffect(() => {
    return () => {
      if (websocketRef.current) {
        websocketRef.current.disconnect();
        websocketRef.current = null;
      }
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
    };
  }, []);

  // Connect WebSocket when chat ID changes
  useEffect(() => {
    if (currentChatId) {
      connectWebSocket(currentChatId);
    } else {
      if (websocketRef.current) {
        websocketRef.current.disconnect();
        websocketRef.current = null;
      }
      setWsStatus('disconnected');
      setProcessingStatus(prev => ({ ...prev, status: 'idle', is_processing: false }));
    }
  }, [currentChatId]);

  const connectWebSocket = useCallback((chatId) => {
    // Disconnect existing
    if (websocketRef.current) {
      websocketRef.current.disconnect();
      websocketRef.current = null;
    }

    if (!chatId) {
      setWsStatus('disconnected');
      setProcessingStatus(prev => ({ ...prev, status: 'idle', is_processing: false }));
      return;
    }

    console.log(`🔌 Creating WebSocket for chat: ${chatId}`);

    // Create new ChatWebSocket instance
    const ws = new ChatWebSocket(chatId, {
      onMessage: (message) => {
        console.log('💬 New message:', message);
        if (message && message.role === 'assistant') {
          setMessages(prev => {
            const exists = prev.some(m => 
              m.id === message.id || 
              (m.role === 'assistant' && m.content === message.content && 
               Math.abs(new Date(m.timestamp) - new Date(message.timestamp)) < 1000)
            );
            if (exists) return prev;
            return [...prev, message];
          });
        }
      },
      
      onStatusUpdate: (data) => {
        console.log('📊 Status update received:', data);
        
        // Update processing status - this drives the progress bar
        const newStatus = {
          status: data.status || 'idle',
          progress: data.progress || 0,
          current_step: data.current_step || '',
          friendly_message: data.friendly_message || '',
          is_ready: data.is_ready || false,
          is_processing: data.is_processing || false,
          is_failed: data.is_failed || false,
          has_error: data.has_error || false,
          error_message: data.error_message || null,
          document_id: data.document_id || null,
          started_at: data.started_at || null,
          completed_at: data.completed_at || null
        };
        
        console.log('Setting processing status:', newStatus);
        setProcessingStatus(newStatus);
        
        if (data.error_message) {
          setError(data.error_message);
        }
      },
      
      onCrawlProgress: (data) => {
        console.log('🕷️ Crawl progress received:', data);
        
        // Update crawled URLs
        if (data.urls) {
          const dedupedUrls = deduplicateUrls(data.urls);
          console.log('Setting crawled URLs:', dedupedUrls.length);
          setCrawledUrls(dedupedUrls);
        }
        
        // Update crawl stats
        const stats = {
          total: data.total || 0,
          completed: data.completed || 0,
          failed: data.failed || 0,
          pending: data.pending || 0,
          processing: data.processing || 0
        };
        console.log('Setting crawl stats:', stats);
        setCrawlStats(stats);
        
        // Update processing status to show crawling progress
        if (data.total > 0) {
          const progress = Math.round((data.completed / data.total) * 100);
          setProcessingStatus(prev => ({
            ...prev,
            status: 'crawling',
            progress: Math.min(progress, 95),
            current_step: `Crawling ${data.completed}/${data.total} pages...`,
            friendly_message: `Found ${data.total} pages, crawling...`,
            is_processing: true,
            is_ready: false
          }));
        }
      },
      
      onCrawlSummary: (data) => {
        console.log('📊 Crawl summary received:', data);
        
        if (data.urls) {
          const dedupedUrls = deduplicateUrls(data.urls);
          setCrawledUrls(dedupedUrls);
        }
        
        setCrawlStats({
          total: data.total_pages_crawled || 0,
          completed: data.total_pages_crawled || 0,
          failed: data.total_pages_failed || 0,
          pending: 0,
          processing: 0
        });
        
        // Update processing status - crawl is complete
        setProcessingStatus(prev => ({
          ...prev,
          status: 'crawling',
          progress: 95,
          current_step: `Crawled ${data.total_pages_crawled || 0} pages`,
          friendly_message: 'Crawl complete, processing content...',
          is_processing: true,
          is_ready: false
        }));
      },
      
      onError: (error) => {
        console.error('❌ WebSocket error:', error);
        const errorMsg = error.error || error.message || 'WebSocket error';
        setError(errorMsg);
        setWsStatus('error');
        
        setProcessingStatus(prev => ({
          ...prev,
          status: 'failed',
          is_failed: true,
          is_processing: false,
          error_message: errorMsg,
          friendly_message: 'Error occurred'
        }));
      },
      
      onConnect: () => {
        console.log('✅ WebSocket connected');
        setWsStatus('connected');
        reconnectAttemptsRef.current = 0;
        // Refresh crawled URLs when WebSocket connects
        if (currentChatId) {
          fetchCrawledUrls(currentChatId);
        }
      },
      
      onDisconnect: () => {
        console.log('❌ WebSocket disconnected');
        setWsStatus('disconnected');
      },
      
      onReconnectAttempt: (attempt) => {
        console.log(`🔄 Reconnect attempt ${attempt}`);
        setWsStatus('connecting');
      },
      
      onComplete: (data) => {
        console.log('✅ Processing complete:', data);
        setProcessingStatus(prev => ({
          ...prev,
          status: 'completed',
          progress: 100,
          is_ready: true,
          is_processing: false,
          friendly_message: 'Ready for questions!'
        }));
      },
      
      onUserJoined: (data) => {
        console.log('👤 User joined:', data);
      },
      
      onUserLeft: (data) => {
        console.log('👤 User left:', data);
      },
      
      onUsersList: (data) => {
        console.log('👥 Users list:', data);
      },
      
      reconnectDelay: RECONNECT_DELAY,
      maxReconnectAttempts: MAX_RECONNECT_ATTEMPTS
    });

    websocketRef.current = ws;
    ws.connect();
    setWsStatus('connecting');
    
  }, [currentChatId]);

  const loadChat = async (chatId) => {
    try {
      const response = await axios.get(`${API_URL}/api/chats/${chatId}`);
      setMessages(response.data.messages || []);
      setCurrentChatId(chatId);
      setError(null);
      await fetchCrawledUrls(chatId);
    } catch (error) {
      console.error('Error loading chat:', error);
      setError('Failed to load chat');
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!inputValue.trim()) return;

    const userMessage = {
      role: 'user',
      content: inputValue,
      timestamp: new Date().toISOString()
    };

    setMessages(prev => [...prev, userMessage]);
    const currentInput = inputValue;
    setInputValue('');
    setIsLoading(true);
    setError(null);

    try {
      const requestBody = {
        content: currentInput,
        chat_id: currentChatId || null
      };

      console.log('Sending request with conversation_id:', requestBody.chat_id);

      const response = await axios.post(`${API_URL}/api/process-link`, requestBody, {
        headers: {
          'Content-Type': 'application/json',
        }
      });

      console.log('Response received:', response.data);

      if (response.data.chat_id) {
        const newChatId = response.data.chat_id;
        setCurrentChatId(newChatId);
        console.log('Conversation ID updated to:', newChatId);
      }

      if (!websocketRef.current || websocketRef.current.getStatus() !== 'connected') {
        if (response.data.message) {
          setMessages(prev => {
            const exists = prev.some(m => 
              m.id === response.data.message.id || 
              (m.role === 'assistant' && m.content === response.data.message.content)
            );
            if (exists) return prev;
            return [...prev, response.data.message];
          });
        }
      }
      
      await fetchChatHistory();
      
    } catch (error) {
      console.error('Error processing input:', error);
      
      let errorDetail = 'Failed to process your request';
      if (error.response?.data?.detail) {
        errorDetail = error.response.data.detail;
      } else if (error.request) {
        errorDetail = 'No response from server. Please check if the backend is running.';
      }
      
      setError(errorDetail);
      
      const errorMessage = {
        role: 'assistant',
        content: `❌ Error: ${errorDetail}`,
        timestamp: new Date().toISOString()
      };
      setMessages(prev => [...prev, errorMessage]);
    } finally {
      setIsLoading(false);
    }
  };

  const createNewChat = () => {
    setMessages([]);
    setCurrentChatId(null);
    setInputValue('');
    setError(null);
    setWsStatus('disconnected');
    setCrawledUrls([]);
    setCrawlStats({ total: 0, completed: 0, failed: 0, pending: 0, processing: 0 });
    setProcessingStatus({
      status: 'idle',
      progress: 0,
      current_step: '',
      friendly_message: '',
      is_ready: false,
      is_processing: false,
      is_failed: false,
      has_error: false,
      error_message: null,
      document_id: null,
      started_at: null,
      completed_at: null
    });
    
    if (websocketRef.current) {
      websocketRef.current.disconnect();
      websocketRef.current = null;
    }
    
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    
    setTimeout(() => inputRef.current?.focus(), 0);
  };

  const deleteChat = async (chatId, e) => {
    e.stopPropagation();
    try {
      await axios.delete(`${API_URL}/api/chats/${chatId}`);
      await fetchChatHistory();
      if (currentChatId === chatId) {
        setMessages([]);
        setCurrentChatId(null);
        setWsStatus('disconnected');
        setCrawledUrls([]);
        setCrawlStats({ total: 0, completed: 0, failed: 0, pending: 0, processing: 0 });
        setProcessingStatus({
          status: 'idle',
          progress: 0,
          current_step: '',
          friendly_message: '',
          is_ready: false,
          is_processing: false,
          is_failed: false,
          has_error: false,
          error_message: null,
          document_id: null,
          started_at: null,
          completed_at: null
        });
        if (websocketRef.current) {
          websocketRef.current.disconnect();
          websocketRef.current = null;
        }
      }
    } catch (error) {
      console.error('Error deleting chat:', error);
      setError('Failed to delete chat');
    }
  };

  // Keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'n') {
        e.preventDefault();
        createNewChat();
      }
      if (e.key === 'Escape' && error) {
        setError(null);
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [error]);

  // Helper to render URL status icon
  const getUrlStatusIcon = (status) => {
    const map = {
      'pending': '⏳',
      'processing': '🔄',
      'completed': '✅',
      'failed': '❌'
    };
    return map[status] || '⏳';
  };

  const getUrlStatusLabel = (status) => {
    const map = {
      'pending': 'Pending',
      'processing': 'Processing',
      'completed': 'Done',
      'failed': 'Failed'
    };
    return map[status] || status;
  };

  return (
    <div className="app">
      {/* Sidebar */}
      <div className={`sidebar ${isSidebarOpen ? 'open' : 'closed'}`}>
        <button className="new-chat-btn" onClick={createNewChat}>
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
            <path d="M8 3v10M3 8h10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
          </svg>
          New chat (Ctrl+N)
        </button>
        
        <div className="chat-history">
          {chatHistory.length === 0 ? (
            <div className="no-chats">No chats yet</div>
          ) : (
            chatHistory.map((chat) => (
              <div
                key={chat.id}
                className={`chat-item ${currentChatId === chat.id ? 'active' : ''}`}
                onClick={() => loadChat(chat.id)}
              >
                <div className="chat-item-content">
                  <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                    <path d="M2 4h12M2 8h8M2 12h4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
                  </svg>
                  <span title={chat.title}>{truncateText(chat.title, 30)}</span>
                </div>
                <button 
                  className="delete-chat-btn"
                  onClick={(e) => deleteChat(chat.id, e)}
                  title="Delete chat"
                >
                  <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                    <path d="M3 3l8 8M11 3l-8 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
                  </svg>
                </button>
              </div>
            ))
          )}
        </div>
        
        <div className="sidebar-footer">
          <div className="ws-indicator">
            {getWsStatusIndicator()}
            <span className="ws-label">
              {wsStatus === 'connected' ? 'Live' : wsStatus === 'connecting' ? 'Connecting...' : wsStatus === 'error' ? 'Error' : 'Disconnected'}
            </span>
          </div>
          <button className="toggle-sidebar" onClick={() => setIsSidebarOpen(!isSidebarOpen)}>
            {isSidebarOpen ? '◀' : '▶'}
          </button>
        </div>
      </div>

      {/* Main Chat Area */}
      <div className="main-chat">
        {/* Header */}
        <header className="chat-header">
          <h1>Link Chat Assistant</h1>
          {currentChatId && (
            <span className="chat-id" title={currentChatId}>
              {displayChatId(currentChatId)}
            </span>
          )}
          {!currentChatId && messages.length === 0 && (
            <span className="chat-id">New Conversation</span>
          )}
        </header>

        {/* Crawled Pages Section - Always show if there are URLs */}
        {currentChatId && crawledUrls.length > 0 && (
          <div className="crawled-pages-section">
            <div className="crawled-pages-header" onClick={() => setShowCrawledUrls(!showCrawledUrls)}>
              <div className="crawled-pages-title">
                <span className="crawled-icon">📄</span>
                <span>Crawled Pages</span>
                <span className="crawled-count">{crawlStats.completed}/{crawledUrls.length}</span>
              </div>
              <div className="crawled-stats">
                {crawlStats.processing > 0 && <span className="stat processing">🔄 {crawlStats.processing}</span>}
                {crawlStats.pending > 0 && <span className="stat pending">⏳ {crawlStats.pending}</span>}
                {crawlStats.failed > 0 && <span className="stat failed">❌ {crawlStats.failed}</span>}
                <span className="toggle-icon">{showCrawledUrls ? '▼' : '▶'}</span>
              </div>
            </div>
            {showCrawledUrls && (
              <div className="crawled-pages-list">
                {crawledUrls.map((url, index) => (
                  <div key={`${url.url}-${index}`} className={`crawled-page-item ${url.status}`}>
                    <span className="url-status">{getUrlStatusIcon(url.status)}</span>
                    <span className="url-title" title={url.url}>
                      {url.url}
                    </span>
                    <span className="url-status-label">{getUrlStatusLabel(url.status)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Processing Status Bar - shows real-time progress */}
        {processingStatus.is_processing && (
          <div className="processing-status-bar">
            <div className="status-row">
              <div className="status-info">
                <span className="status-emoji">{getStatusEmoji(processingStatus.status)}</span>
                <span 
                  className="status-label"
                  style={{ color: getStatusColor(processingStatus.status) }}
                >
                  {getStatusLabel(processingStatus.status)}
                </span>
                {processingStatus.progress > 0 && (
                  <span className="status-percent">{processingStatus.progress}%</span>
                )}
              </div>
              {processingStatus.started_at && (
                <span className="status-time">
                  Started: {formatTime(processingStatus.started_at)}
                </span>
              )}
            </div>
            
            {/* Progress bar */}
            {processingStatus.progress > 0 && processingStatus.progress < 100 && (
              <div className="progress-track">
                <div 
                  className="progress-fill"
                  style={{ width: `${processingStatus.progress}%` }}
                />
              </div>
            )}
            
            {processingStatus.current_step && (
              <div className="status-step">
                📌 {processingStatus.current_step}
              </div>
            )}
            
            {processingStatus.friendly_message && processingStatus.friendly_message !== processingStatus.current_step && (
              <div className="status-message">
                {processingStatus.friendly_message}
              </div>
            )}
          </div>
        )}

        {/* Completed Status */}
        {processingStatus.is_ready && !processingStatus.is_processing && (
          <div className="status-bar-complete">
            <span className="complete-icon">✅</span>
            <span className="complete-text">Ready for questions!</span>
          </div>
        )}

        {/* Failed Status */}
        {processingStatus.is_failed && (
          <div className="status-bar-failed">
            <span className="failed-icon">❌</span>
            <span className="failed-text">
              {processingStatus.error_message || 'Processing failed'}
            </span>
          </div>
        )}

        {/* Messages */}
        <div className="messages-container">
          {messages.length === 0 ? (
            <div className="welcome-screen">
              <div className="welcome-icon">🔗</div>
              <h2>What would you like to know?</h2>
              <p>Paste a link or type a message to get started</p>
              <div className="example-links">
                <button onClick={() => setInputValue('https://example.com/article')}>
                  📰 Summarize an article
                </button>
                <button onClick={() => setInputValue('https://github.com/repo')}>
                  💻 Analyze a repository
                </button>
                <button onClick={() => setInputValue('https://youtube.com/watch')}>
                  🎥 Extract video insights
                </button>
                <button onClick={() => setInputValue('What is machine learning?')}>
                  ❓ Ask a question
                </button>
              </div>
            </div>
          ) : (
            messages.map((message, index) => (
              <div key={message.id || index} className={`message ${message.role}`}>
                <div className="message-avatar">
                  {message.role === 'user' ? '👤' : '🤖'}
                </div>
                <div className="message-content">
                  <div className="message-text">
                    {renderMessageContent(message.content)}
                  </div>
                  <div className="message-time">
                    {formatDate(message.timestamp)}
                  </div>
                </div>
              </div>
            ))
          )}
          {isLoading && (
            <div className="message assistant">
              <div className="message-avatar">🤖</div>
              <div className="message-content">
                <div className="typing-indicator">
                  <span></span>
                  <span></span>
                  <span></span>
                </div>
              </div>
            </div>
          )}
          {error && !isLoading && (
            <div className="message assistant error-message">
              <div className="message-avatar">⚠️</div>
              <div className="message-content">
                <div className="message-text" style={{ color: '#ff6b6b' }}>
                  {error}
                </div>
                <button 
                  className="dismiss-error"
                  onClick={() => setError(null)}
                  style={{
                    background: 'none',
                    border: 'none',
                    color: '#8e8ea0',
                    cursor: 'pointer',
                    fontSize: '12px',
                    marginTop: '4px',
                    textDecoration: 'underline'
                  }}
                >
                  Dismiss
                </button>
              </div>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>

        {/* Input Area */}
        <div className="input-container">
          <form onSubmit={handleSubmit} className="input-form">
            <input
              ref={inputRef}
              type="text"
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              placeholder="Paste a link or type a message..."
              className="chat-input"
              disabled={isLoading}
            />
            <button 
              type="submit" 
              className="send-button"
              disabled={isLoading || !inputValue.trim()}
            >
              {isLoading ? '⏳' : '➤'}
            </button>
          </form>
          <div className="input-footer">
            <span>
              {currentChatId 
                ? `Conversation: ${displayChatId(currentChatId)}` 
                : 'New conversation'}
            </span>
            <span className="ws-status-text">
              {wsStatus === 'connected' ? '● Live' : wsStatus === 'connecting' ? '◐ Connecting...' : wsStatus === 'error' ? '⚠️ Error' : '○ Disconnected'}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

export default App;