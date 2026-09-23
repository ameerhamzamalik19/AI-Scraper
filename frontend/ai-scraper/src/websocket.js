// src/websocket.js
export class ChatWebSocket {
  constructor(chatId, options = {}) {
    this.chatId = chatId;
    this.options = {
      onMessage: options.onMessage || (() => {}),
      onStatusUpdate: options.onStatusUpdate || (() => {}),
      onCrawlProgress: options.onCrawlProgress || (() => {}),      // NEW
      onCrawlSummary: options.onCrawlSummary || (() => {}),        // NEW
      onError: options.onError || (() => {}),
      onConnect: options.onConnect || (() => {}),
      onDisconnect: options.onDisconnect || (() => {}),
      onReconnectAttempt: options.onReconnectAttempt || (() => {}),
      onUserJoined: options.onUserJoined || (() => {}),            // NEW
      onUserLeft: options.onUserLeft || (() => {}),                // NEW
      onUsersList: options.onUsersList || (() => {}),              // NEW
      onComplete: options.onComplete || (() => {}),                // NEW
      reconnectDelay: options.reconnectDelay || 3000,
      maxReconnectAttempts: options.maxReconnectAttempts || 5,
    };
    
    this.ws = null;
    this.reconnectAttempts = 0;
    this.isConnected = false;
    this.isConnecting = false;
    this.reconnectTimer = null;
    this.shouldReconnect = true;
  }

  connect() {
    if (this.isConnecting || this.isConnected) return;
    
    this.isConnecting = true;
    
    const wsUrl = `ws://127.0.0.1:8000/ws/${this.chatId}`;
    console.log(`🔌 Connecting WebSocket: ${wsUrl}`);
    
    try {
      this.ws = new WebSocket(wsUrl);
      
      this.ws.onopen = () => {
        this.isConnected = true;
        this.isConnecting = false;
        this.reconnectAttempts = 0;
        console.log(`✅ WebSocket connected for chat: ${this.chatId}`);
        this.options.onConnect();
        
        // Send join message
        this.send({ type: 'join' });
      };
      
      this.ws.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          this.handleMessage(payload);
        } catch (error) {
          console.warn('Failed to parse WebSocket message:', error);
        }
      };
      
      this.ws.onclose = (event) => {
        this.isConnected = false;
        this.isConnecting = false;
        console.log(`❌ WebSocket closed: ${this.chatId}`, event.code);
        this.options.onDisconnect();
        
        if (this.shouldReconnect && event.code !== 1000 && event.code !== 1001) {
          this.scheduleReconnect();
        }
      };
      
      this.ws.onerror = (error) => {
        console.error('❌ WebSocket error:', error);
        this.options.onError(error);
      };
      
    } catch (error) {
      console.error('❌ Failed to create WebSocket:', error);
      this.isConnecting = false;
      this.options.onError(error);
      this.scheduleReconnect();
    }
  }

  handleMessage(payload) {
    const { type, data, message } = payload;
    
    switch (type) {
      // Chat messages
      case 'message':
      case 'new_message':
        this.options.onMessage(message || data);
        break;
      
      // Status updates
      case 'status_update':
      case 'progress_update':
        this.options.onStatusUpdate(data);
        break;
      
      // Crawl progress (real-time URL tracking)
      case 'crawl_progress':
        this.options.onCrawlProgress(data || payload);
        break;
      
      // Crawl summary (final)
      case 'crawl_summary':
        this.options.onCrawlSummary(data || payload);
        break;
      
      // User presence
      case 'user_joined':
        this.options.onUserJoined(data);
        break;
      
      case 'user_left':
        this.options.onUserLeft(data);
        break;
      
      case 'users_list':
        this.options.onUsersList(data);
        break;
      
      // Completion / errors
      case 'complete':
        this.options.onComplete(data);
        break;
      
      case 'error':
        this.options.onError(data);
        break;
      
      // Heartbeat
      case 'pong':
        // Ignore
        break;
      
      default:
        console.log('Unknown message type:', type, payload);
    }
  }

  scheduleReconnect() {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    
    if (this.reconnectAttempts >= this.options.maxReconnectAttempts) {
      console.log('⚠️ Max reconnection attempts reached');
      this.options.onError({ error: 'Unable to reconnect to server' });
      return;
    }
    
    this.reconnectAttempts++;
    const delay = this.options.reconnectDelay * Math.min(this.reconnectAttempts, 5);
    
    console.log(`🔄 Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`);
    this.options.onReconnectAttempt(this.reconnectAttempts);
    
    this.reconnectTimer = setTimeout(() => {
      this.connect();
    }, delay);
  }

  send(data) {
    if (this.isConnected && this.ws) {
      try {
        this.ws.send(JSON.stringify(data));
        return true;
      } catch (error) {
        console.error('Failed to send WebSocket message:', error);
        return false;
      }
    }
    return false;
  }

  disconnect() {
    this.shouldReconnect = false;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.ws.close(1000, 'User disconnected');
      this.ws = null;
    }
    this.isConnected = false;
    this.isConnecting = false;
  }

  getStatus() {
    if (this.isConnected) return 'connected';
    if (this.isConnecting) return 'connecting';
    return 'disconnected';
  }
}

export default ChatWebSocket;