import React, { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import './App.css';

function App() {
  const [inputValue, setInputValue] = useState('');
  const [messages, setMessages] = useState([]);
  const [chatHistory, setChatHistory] = useState([]);
  const [currentChatId, setCurrentChatId] = useState(null); // This is the conversation_id (UUID)
  const [isLoading, setIsLoading] = useState(false);
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);
  const [error, setError] = useState(null);
  const messagesEndRef = useRef(null);
  const inputRef = useRef(null);
  const websocketRef = useRef(null);
  const websocketConnectedRef = useRef(false);
  const API_URL = 'http://localhost:8000';

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  const fetchChatHistory = async () => {
    try {
      const response = await axios.get(`${API_URL}/api/chats`);
      console.log(response.data);
      setChatHistory(response.data);
    } catch (error) {
      console.error('Error fetching chat history:', error);
      setError('Failed to load chat history');
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

  useEffect(() => {
    if (!currentChatId) return undefined;

    const websocketUrl = `${API_URL.replace(/^http/, 'ws')}/ws/${currentChatId}`;
    const websocket = new WebSocket(websocketUrl);
    websocketRef.current = websocket;

    websocket.onopen = () => {
      websocketConnectedRef.current = true;
      websocket.send('join');
    };

    websocket.onmessage = (event) => {
      const payload = JSON.parse(event.data);
      if (payload.type === 'message' && payload.message) {
        setMessages(prev => [...prev, payload.message]);
      }
    };

    websocket.onerror = () => {
      websocketConnectedRef.current = false;
    };

    websocket.onclose = () => {
      websocketConnectedRef.current = false;
      if (websocketRef.current === websocket) {
        websocketRef.current = null;
      }
    };

    return () => {
      websocketConnectedRef.current = false;
      websocket.close();
      if (websocketRef.current === websocket) {
        websocketRef.current = null;
      }
    };
  }, [currentChatId]);

  const loadChat = async (chatId) => {
    try {
      const response = await axios.get(`${API_URL}/api/chats/${chatId}`);
      setMessages(response.data.messages);
      setCurrentChatId(chatId); // Set the conversation ID (UUID)
      setError(null);
    } catch (error) {
      console.error('Error loading chat:', error);
      setError('Failed to load chat');
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!inputValue.trim()) return;

    // Add user message to UI immediately
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
      // Prepare request body - send conversation_id (chat_id) if it exists, otherwise null
      const requestBody = {
        content: currentInput,
        chat_id: currentChatId || null // Send null if no conversation exists
      };

      console.log('Sending request with conversation_id:', requestBody.chat_id);

      const response = await axios.post(`${API_URL}/api/process-link`, requestBody, {
        headers: {
          'Content-Type': 'application/json',
        }
      });

      console.log('Response received:', response.data);

      // IMPORTANT: Update the conversation ID with the UUID from backend
      if (response.data.chat_id) {
        const newChatId = response.data.chat_id;
        setCurrentChatId(newChatId);
        console.log('Conversation ID (UUID) updated to:', newChatId);
      }

      // A new chat has no room until the backend returns its id, so use the
      // HTTP response for that first assistant message. Existing rooms stream it.
      if (!websocketConnectedRef.current) {
        setMessages(prev => [...prev, response.data.message]);
      }
      
      // Log user ID
      if (response.data.user_id) {
        console.log('User ID:', response.data.user_id);
      }
      
      // Log detection results for debugging
      if (response.data.detection) {
        console.log('Detection results:', response.data.detection);
      }
      
      // Refresh chat history to show new/updated chat
      await fetchChatHistory();
      
    } catch (error) {
      console.error('Error processing input:', error);
      
      // Log detailed error information
      if (error.response) {
        console.error('Error response data:', error.response.data);
        console.error('Error response status:', error.response.status);
        
        // Show detailed error message
        const errorDetail = error.response.data?.detail || 'Failed to process your request';
        setError(errorDetail);
        
        const errorMessage = {
          role: 'assistant',
          content: `❌ Error: ${errorDetail}`,
          timestamp: new Date().toISOString()
        };
        setMessages(prev => [...prev, errorMessage]);
      } else if (error.request) {
        // The request was made but no response was received
        console.error('No response received:', error.request);
        setError('No response from server. Please check if the backend is running.');
        
        const errorMessage = {
          role: 'assistant',
          content: '❌ Error: Cannot connect to the server. Please make sure the backend is running on port 8000.',
          timestamp: new Date().toISOString()
        };
        setMessages(prev => [...prev, errorMessage]);
      } else {
        // Something happened in setting up the request
        console.error('Error setting up request:', error.message);
        setError('Failed to send request');
        
        const errorMessage = {
          role: 'assistant',
          content: `❌ Error: ${error.message}`,
          timestamp: new Date().toISOString()
        };
        setMessages(prev => [...prev, errorMessage]);
      }
    } finally {
      setIsLoading(false);
    }
  };

  const createNewChat = () => {
    // Reset everything for a new conversation
    setMessages([]);
    setCurrentChatId(null); // Set to null - backend will create new UUID
    setInputValue('');
    setError(null);
    if (inputRef.current) {
      inputRef.current.focus();
    }
    console.log('New conversation started - UUID will be created on first message');
  };

  const deleteChat = async (chatId, e) => {
    e.stopPropagation();
    try {
      await axios.delete(`${API_URL}/api/chats/${chatId}`);
      await fetchChatHistory();
      // If we're deleting the current chat, reset the state
      if (currentChatId === chatId) {
        setMessages([]);
        setCurrentChatId(null);
        console.log('Current conversation deleted, reset to null');
      }
    } catch (error) {
      console.error('Error deleting chat:', error);
      setError('Failed to delete chat');
    }
  };

  // Safe date formatting function
  const formatDate = (dateString) => {
    if (!dateString) return 'Just now';
    
    try {
      const date = new Date(dateString);
      
      // Check if date is valid
      if (isNaN(date.getTime())) {
        console.warn('Invalid date:', dateString);
        return 'Just now';
      }
      
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
    } catch (error) {
      console.error('Error formatting date:', error);
      return 'Just now';
    }
  };

  // Truncate long messages for display
  const truncateText = (text, maxLength = 40) => {
    if (!text) return '';
    if (text.length <= maxLength) return text;
    return text.substring(0, maxLength) + '...';
  };

  // Render message with line breaks
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

  // Display shortened UUID for display
  const displayChatId = (chatId) => {
    if (!chatId) return 'New Conversation';
    const parts = chatId.split('-');
    if (parts.length === 5) {
      return `${parts[0]}-${parts[1]}-...-${parts[4]}`;
    }
    return chatId.substring(0, 8) + '...';
  };

  return (
    <div className="app">
      {/* Sidebar */}
      <div className={`sidebar ${isSidebarOpen ? 'open' : 'closed'}`}>
        <button className="new-chat-btn" onClick={createNewChat}>
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
            <path d="M8 3v10M3 8h10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
          </svg>
          New chat
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
              Conversation: {displayChatId(currentChatId)}
            </span>
          )}
          {!currentChatId && messages.length === 0 && (
            <span className="chat-id">New Conversation</span>
          )}
        </header>

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
              <div key={index} className={`message ${message.role}`}>
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
          {error && (
            <div className="message assistant">
              <div className="message-avatar">⚠️</div>
              <div className="message-content">
                <div className="message-text" style={{ color: '#ff6b6b' }}>
                  {error}
                </div>
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
              💡 {currentChatId 
                ? `Conversation: ${displayChatId(currentChatId)} (User ID: 1)` 
                : 'New conversation - UUID will be created on first message (User ID: 1)'}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

export default App;